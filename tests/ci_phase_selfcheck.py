"""Diagnostic acceptance checks, separate from the unchanged regression census."""
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from ci_phase_suite import capture_failure, result_class


class DiagnosticChecks(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('isolated_phase_probe',ROOT/'tests/ci_phase_probe.py')
        self.probe=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.probe)
        self.probe.flush=lambda:True
        self.env=dict(os.environ)
        os.environ.update(WB_PHASE_TARGET_ACTIVE='1',WB_PHASE_TEST='test_fixture.Example.test_private',WB_PHASE_OCCURRENCE='1')
        self.addCleanup(lambda:(os.environ.clear(),os.environ.update(self.env)))
    def rows(self):
        rows=[]
        while not self.probe._queue.empty():rows.append(self.probe._queue.get_nowait())
        return rows
    def test_inactive_observer_preserves_original_call_and_returns(self):
        os.environ['WB_PHASE_TARGET_ACTIVE']='0'
        seen=[]
        owner=types.SimpleNamespace(fn=lambda value:(seen.append(value),value)[1])
        self.probe._wrap(owner,'fn')
        original=object()
        self.assertIs(owner.fn(original),original)
        self.assertEqual(seen,[original])
        self.assertEqual(self.rows(),[])
    def test_job_state_excludes_payloads_and_unrecognized_progress_keys(self):
        private='PRIVATE-DO-NOT-LOG'
        raw={'job_id':'job-'+'0'*64,'state':'running','phase':'preparing','settings':private,
             'heartbeat':10,'revision':2,'progress':{'completed_seeds':3,private:123,'body':private}}
        owner=types.SimpleNamespace(change=lambda:raw)
        self.probe._wrap(owner,'change')
        self.assertIs(owner.change(),raw)
        rows=self.rows()
        self.assertNotIn(private,json.dumps(rows))
        state=next(r for r in rows if r['event']=='job.state')
        self.assertEqual(state['progress'],{'completed_seeds':3})
    def test_native_request_id_is_correlated_by_hash_without_plaintext(self):
        private='PRIVATE-REQUEST-ID'
        class Jobs:
            def submit(self,request):return {'job_id':'job-'+'0'*64,'settings':private}
        self.probe._wrap(Jobs,'submit')
        Jobs().submit({'request_id':private})
        rows=self.rows()
        self.assertNotIn(private,json.dumps(rows))
        event=next(r for r in rows if r['event']=='job.correlate')
        self.assertEqual(len(event['native_request_sha256']),64)
    def test_untrusted_header_is_not_recorded(self):
        class Handler:
            headers={'X-WB-Phase-Request':'PRIVATE-HEADER'}
            def do_POST(self):return 'ok'
        self.probe._wrap(Handler,'do_POST')
        self.assertEqual(Handler().do_POST(),'ok')
        self.assertNotIn('PRIVATE-HEADER',json.dumps(self.rows()))
    def test_metadata_never_stringifies_arbitrary_objects(self):
        class Private:
            def __str__(self):raise AssertionError('must not stringify')
            def __repr__(self):raise AssertionError('must not repr')
        self.assertIsNone(self.probe._json_safe(Private()))
        self.assertIsNone(self.probe._json_safe(float('nan')))
        self.assertIsNone(self.probe._number(float('inf')))
        self.assertEqual(self.probe._number(10**400),10**400)
    def test_inflight_span_retains_test_and_occurrence_after_next_test_starts(self):
        old=os.environ['WB_PHASE_TEST']
        def running():
            os.environ.update(WB_PHASE_TEST='test_next.Example.test_next',WB_PHASE_OCCURRENCE='2',WB_PHASE_TARGET_ACTIVE='0')
            self.probe.emit('still.inflight')
            return 'same-result'
        owner=types.SimpleNamespace(fn=running)
        self.probe._wrap(owner,'fn')
        self.assertEqual(owner.fn(),'same-result')
        self.assertEqual({(r['test'],r['occurrence']) for r in self.rows()},{(old,1)})
    def test_late_http_handler_uses_original_request_case(self):
        old=sorted(self.probe.TARGETS)[0]
        self.probe._request_cases['0'*32]=(old,1)
        os.environ.update(WB_PHASE_TEST='test_next.Example.test_next',WB_PHASE_OCCURRENCE='2',WB_PHASE_TARGET_ACTIVE='0')
        class Handler:
            headers={'X-WB-Phase-Request':'0'*32}
            def do_POST(self):return 'same-response'
        self.probe._wrap(Handler,'do_POST')
        self.assertEqual(Handler().do_POST(),'same-response')
        self.assertEqual({(r['test'],r['occurrence']) for r in self.rows()},{(old,1)})
    def test_analysis_distinguishes_reused_pid_by_os_creation_time(self):
        with tempfile.TemporaryDirectory(prefix='wb-phase-analysis-') as temporary:
            root=Path(temporary)
            (root/'phases').mkdir()
            rows=[]
            target=sorted(self.probe.TARGETS)[0]
            for created in (10,20):
                common=dict(pid=100,process_created=created,tid=1,span='100-1',test=target,occurrence=1,phase='worker.prepare')
                rows.extend([dict(common,event='begin',mono=created,parent=None),
                             dict(common,event='end',mono=created+1,seconds=1,error=None)])
            (root/'phases/100.jsonl').write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            result=subprocess.run([sys.executable,'-B',str(ROOT/'tests/ci_phase_analyze.py'),str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads((root/'analysis.json').read_text())
            self.assertEqual({r['process_created'] for r in report['summary']},{10,20})
            self.assertEqual(report['unfinished'],[])
    def test_failure_snapshot_precedes_original_cleanup_and_excludes_settings(self):
        probe=self.probe
        order=[]
        with tempfile.TemporaryDirectory(prefix='wb-phase-failure-') as temporary:
            base=Path(temporary)
            folder=base/'control/jobs'/('job-'+'0'*64)
            folder.mkdir(parents=True)
            raw={'job_id':folder.name,'state':'running','phase':'preparing','heartbeat':1,'revision':2,'settings':'PRIVATE-SETTINGS'}
            (folder/'job.json').write_text(json.dumps(raw),encoding='utf-8')
            class Example(unittest.TestCase):
                def setUp(self):
                    self.base=base
                    self.addCleanup(lambda:order.append(('cleanup',len(probe._queue.queue))))
                def test_expected_failure(self):self.fail('intentional diagnostic acceptance failure')
            stream=io.StringIO()
            result=unittest.TextTestRunner(stream=stream,resultclass=result_class(unittest.TextTestResult,probe)).run(
                unittest.TestSuite([Example('test_expected_failure')]))
            self.assertEqual(len(result.failures),1)
            rows=self.rows()
            before=next(i for i,r in enumerate(rows) if r['event']=='test.failure_before_cleanup')
            snapshot=next(i for i,r in enumerate(rows) if r['event']=='failure.owned_jobs')
            self.assertLess(before,snapshot)
            self.assertGreater(order[0][1],snapshot)
            self.assertNotIn('PRIVATE-SETTINGS',json.dumps(rows))
    def test_failure_snapshot_skips_paths_outside_owned_temporary_root(self):
        test=types.SimpleNamespace(base=ROOT)
        capture_failure(self.probe,test,'failure')
        rows=self.rows()
        self.assertTrue(any(r['event']=='snapshot.unavailable' for r in rows))
        self.assertNotIn(str(ROOT),json.dumps(rows))
    def test_nested_census_fixture_keeps_original_failure_without_observer_events(self):
        from regression_partition import RecordingResult
        class Fails(unittest.TestCase):
            def runTest(self):self.fail('intentional nested census fixture')
        events=io.StringIO()
        before={key:os.environ.get(key) for key in
                ('WB_PHASE_TEST','WB_PHASE_OCCURRENCE','WB_PHASE_TARGET_ACTIVE')}
        cls=result_class(RecordingResult,self.probe,owned_events='owned-suite-events.jsonl')
        runner=unittest.TextTestRunner(stream=io.StringIO(),resultclass=lambda *a,**kw:
                                      cls(*a,events=events,**kw))
        result=runner.run(unittest.TestSuite([Fails()]))
        self.assertEqual(len(result.failures),1)
        self.assertEqual([json.loads(line)['kind'] for line in events.getvalue().splitlines()],
                         ['start','failure','stop'])
        self.assertEqual(self.rows(),[])
        self.assertEqual(before,{key:os.environ.get(key) for key in before})


if __name__=='__main__':unittest.main(verbosity=2)
