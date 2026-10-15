"""Atomic job snapshots remain readable during a real cross-process durable write."""
import http.client
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from execution import worker
from execution.jobs import JobStore
from execution.provenance import atomic_json, canonical, sha256, open_atomic_read, read_atomic_json
from viewer.server import ViewerHandler, ViewerServer

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform in ('win32', 'linux'), 'OS file lock contract')
class JobStorageReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-job-storage-read-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.jobs = JobStore(SimpleNamespace(control=self.base / 'control'))
        self.folder = self.jobs.root / 'job-storage-read'
        self.folder.mkdir(parents=True)
        entry = self.folder / 'worker.py'
        shutil.copyfile(worker.__file__, entry)
        request = {'kind':'evolve', 'request_id':'storage-read'}
        atomic_json(self.folder / 'request.json', request)
        self.original = dict(schema_version=1, job_id=self.folder.name, request_id='storage-read',
            request_hash=sha256(canonical(request)), nonce='fixture-nonce',
            entrypoint=str(entry.resolve()), worker_sha256=sha256(entry.read_bytes()),
            launch_identity=worker.process_identity(os.getpid()), receipt=None,
            state='running', phase='running', revision=0, created_at=time.time(),
            wall_seconds=900, cancel_requested_at=None, cancel_grace_seconds=30,
            updated_at=time.time(), progress={'completed_individuals':0})
        atomic_json(self.folder / 'job.json', self.original)
        self.server = ViewerServer(('127.0.0.1',0), ViewerHandler)
        self.server.job_store = self.jobs
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval':0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/jobs/' + self.folder.name)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def paused_write(self, fsync_number):
        # Gate the actual worker.change fsync, rather than replacing GET or
        # pretending a 409 is success. The writer retains the OS lock.
        script = self.base / 'paused_writer.py'
        script.write_text('''from pathlib import Path
import sys,time
from execution import worker
jobs,folder,gate,release=map(Path,sys.argv[1:5])
target=int(sys.argv[5])
original=worker.os.fsync
count=0
def fsync(fd):
    global count
    count+=1
    if count==target:
        gate.touch()
        deadline=time.monotonic()+10
        while not release.exists():
            if time.monotonic()>=deadline:
                raise TimeoutError('test writer gate was not released')
            time.sleep(.01)
    return original(fd)
worker.os.fsync=fsync
worker.change(jobs,folder,'fixture-nonce',progress={'completed_individuals':1})
''', encoding='utf-8')
        gate, release = self.base / 'write-paused', self.base / 'write-release'
        child = subprocess.Popen([sys.executable,'-B',str(script),str(self.jobs.root),
            str(self.folder),str(gate),str(release),str(fsync_number)], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH':str(ROOT)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline=time.monotonic()+5
            while not gate.exists():
                if child.poll() is not None or time.monotonic()>=deadline:
                    self.fail('writer did not reach the fsync gate')
                time.sleep(.01)
            status, value = self.get()
            self.assertEqual(status,200,value)
            self.assertFalse(release.exists(), 'GET must complete while the writer still holds its lock')
            self.assertEqual(value['state'],'running')
            self.assertEqual(value['reconciliation'],'confirmed')
            expected_revision=0 if fsync_number==1 else 1
            self.assertEqual(value['revision'],expected_revision)
            self.assertEqual(value['progress']['completed_individuals'],expected_revision)
        finally:
            release.touch()
            stdout,stderr=child.communicate(timeout=5)
        self.assertEqual(child.returncode,0,(stdout,stderr))
        status,value=self.get()
        self.assertEqual(status,200,value)
        self.assertEqual(value['revision'],1)
        self.assertEqual(value['progress']['completed_individuals'],1)
        events=[json.loads(line) for line in (self.folder/'events.jsonl').read_text().splitlines()]
        self.assertEqual([event['revision'] for event in events],[1])

    def test_get_reads_previous_complete_snapshot_while_job_file_fsync_is_paused(self):
        self.paused_write(1)

    def test_get_reads_published_complete_snapshot_while_event_fsync_is_paused(self):
        self.paused_write(2)

    def test_reconciliation_rereads_a_late_worker_claim_under_the_write_lock(self):
        self.stop_server()  # Isolate direct storage calls from server maintenance.
        queued = {**self.original, 'state':'queued', 'launch_identity':None,
                  'created_at':time.time()-self.jobs.startup_timeout-1}
        atomic_json(self.folder/'job.json',queued)
        original_lock=self.jobs._lock

        @contextmanager
        def late_claim(**kwargs):
            worker.change(self.jobs.root,self.folder,'fixture-nonce',state='running',
                          launch_identity=self.original['launch_identity'])
            with original_lock(**kwargs):
                yield

        with patch.object(self.jobs,'_lock',side_effect=late_claim):
            value=self.jobs.get(self.folder.name)
        self.assertEqual(value['state'],'running')
        self.assertEqual(value['reconciliation'],'confirmed')
        self.assertEqual(value['revision'],1)
        self.assertEqual(json.loads((self.folder/'job.json').read_text())['state'],'running')

    def test_unconfirmed_launch_still_persists_interruption_under_the_write_lock(self):
        self.stop_server()
        queued = {**self.original, 'state':'queued', 'launch_identity':None,
                  'created_at':time.time()-self.jobs.startup_timeout-1}
        atomic_json(self.folder/'job.json',queued)
        with patch.object(self.jobs,'_lock',wraps=self.jobs._lock) as locked:
            value=self.jobs.get(self.folder.name)
        locked.assert_called_once_with(operation='jobs.get')
        self.assertEqual(value['state'],'interrupted')
        self.assertEqual(value['error']['code'],'launch_unconfirmed')
        self.assertEqual(json.loads((self.folder/'job.json').read_text())['state'],'interrupted')

    def test_atomic_read_still_rejects_changed_request_integrity(self):
        atomic_json(self.folder/'request.json',{'kind':'evolve','request_id':'tampered'})
        status,value=self.get()
        self.assertEqual(status,422,value)
        self.assertEqual(value['code'],'snapshot_changed')

    def test_open_snapshot_allows_replacement_and_retains_one_complete_revision(self):
        self.stop_server()
        path=self.folder/'job.json'
        replacement={**self.original,'revision':1,'progress':{'completed_individuals':1}}
        with open_atomic_read(path) as old:
            # Execute the exact standalone writer's atomic publication while
            # the reader still holds the old file handle (SHARE_DELETE on NT).
            worker.atomic(path,replacement)
            self.assertEqual(json.load(old),self.original)
            self.assertEqual(read_atomic_json(path),replacement)
        self.assertTrue(path.is_file())
        self.assertEqual(read_atomic_json(path),replacement)

    def test_worker_publication_failure_keeps_previous_complete_snapshot(self):
        self.stop_server()
        path=self.folder/'job.json'
        with patch.object(worker,'replace_job_snapshot',side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                worker.atomic(path,{**self.original,'revision':1})
        self.assertEqual(read_atomic_json(path),self.original)
        self.assertEqual(list(self.folder.glob('.job.json-*')),[])
    def test_parent_publication_failure_stays_bounded_and_keeps_previous_snapshot(self):
        self.stop_server()
        path=self.folder/'job.json'
        error=PermissionError('persistent replacement denial')
        error.winerror=5
        with patch.object(worker,'replace_job_snapshot',side_effect=error) as publish:
            with self.assertRaises(PermissionError):
                atomic_json(path,{**self.original,'revision':1})
        self.assertEqual(publish.call_count,6)
        self.assertEqual(read_atomic_json(path),self.original)
        self.assertEqual(list(self.folder.glob('.job.json-*')),[])
