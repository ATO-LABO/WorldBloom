"""CLI teardown owns its directory and never redispatches a model request."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

from execution.generation import transport
from execution import worker
from execution.cli_workspace import CLIWorkspace, directory_holders
from execution.cli_workspace import close_cli_handles
from execution.generation import ContainmentError
from execution.generation import BoundaryError


class CLIHandleProtocolTests(unittest.TestCase):
    def test_guardian_proxy_closes_its_captured_pipes_without_a_stderr_attribute(self):
        stdin,stdout=Mock(),Mock()
        child=SimpleNamespace(stdin=stdin,stdout=stdout)
        with patch('execution.cli_workspace.os.name','posix'):
            close_cli_handles(child)
        stdin.close.assert_called_once_with()
        stdout.close.assert_called_once_with()


@unittest.skipUnless(os.name=='nt','Windows CWD handle lifecycle')
class CLIWorkspaceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wb-cli-cleanup-test-')
        self.base=Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def test_completed_cli_closes_handles_before_waiting_for_directory_release(self):
        original=tempfile.mkdtemp
        holder=None
        timer=None
        directory=None
        release=self.base/'release'
        gate=self.base/'held'
        program='''import os,sys,time;from pathlib import Path
os.chdir(sys.argv[1]);Path(sys.argv[2]).touch()
deadline=time.monotonic()+10
while not Path(sys.argv[3]).exists() and time.monotonic()<deadline:time.sleep(.01)
'''
        def make(*args,**kwargs):
            nonlocal holder,directory
            kwargs['dir']=str(self.base)
            # TemporaryDirectory passes dir positionally on Python 3.13.
            if len(args)>2:args=(*args[:2],str(self.base),*args[3:]);kwargs.pop('dir')
            directory=Path(original(*args,**kwargs)).resolve()
            self.assertTrue(directory.is_relative_to(self.base))
            holder=subprocess.Popen([sys.executable,'-B','-c',program,str(directory),str(gate),str(release)])
            deadline=time.monotonic()+5
            while not gate.exists():
                if holder.poll() is not None or time.monotonic()>deadline:
                    self.fail('the controlled directory holder did not start')
                time.sleep(.01)
            return str(directory)
        seen=[]
        original_finish=worker._WindowsProcessTree.finish
        original_launch=worker._WindowsProcessTree.launch
        original_cleanup=CLIWorkspace.cleanup
        launched=[]
        def launch(tree,*args,**kwargs):
            child=original_launch(tree,*args,**kwargs)
            launched.append(child)
            return child
        def cleanup(space):
            self.assertEqual(len(launched),1)
            self.assertTrue(launched[0].stdin.closed)
            self.assertTrue(launched[0].stdout.closed)
            self.assertTrue(launched[0]._handle.closed)
            return original_cleanup(space)
        def finish(tree):
            nonlocal timer
            original_finish(tree)
            seen.append('owned_processes_terminated')
            # Retain the independent directory handle past actual Job Object
            # termination, then release it. It must not cause another CLI call.
            timer=threading.Timer(.15,release.touch)
            timer.start()
        try:
            with patch('execution.generation.tempfile.mkdtemp',side_effect=make), \
                 patch.object(worker._WindowsProcessTree,'finish',finish), \
                 patch.object(worker._WindowsProcessTree,'launch',launch), \
                 patch.object(CLIWorkspace,'cleanup',cleanup):
                value=transport({'limits':{'max_saved_response_bytes':32,'call_timeout_seconds':2},
                    'prompt':'fixed fixture only'},[sys.executable,'-B','-c',"print('complete')"])
            self.assertEqual(value.raw,b'complete\r\n')
            self.assertEqual(seen,['owned_processes_terminated'])
            self.assertFalse(directory.exists())
        finally:
            if timer is not None:
                timer.cancel()
                timer.join(timeout=1)
            release.touch()
            if holder is not None:holder.wait(timeout=5)
            if directory is not None and directory.exists():
                self.assertTrue(directory.resolve().is_relative_to(self.base))
                __import__('shutil').rmtree(directory)

    def test_persistent_directory_holder_fails_within_the_supplied_budget(self):
        space=CLIWorkspace()
        path=Path(space.name).resolve()
        gate,release=self.base/'persistent-held',self.base/'persistent-release'
        child=subprocess.Popen([sys.executable,'-B','-c',
            "import os,sys,time;from pathlib import Path;os.chdir(sys.argv[1]);"
            "Path(sys.argv[2]).touch();"
            "\nwhile not Path(sys.argv[3]).exists():time.sleep(.01)",str(path),str(gate),str(release)])
        try:
            deadline=time.monotonic()+5
            while not gate.exists():
                if child.poll() is not None or time.monotonic()>deadline:self.fail('holder failed to start')
                time.sleep(.01)
            self.assertIn(child.pid,directory_holders(path)['holder_pids'])
            space.cleanup_deadline=time.monotonic()+.03
            with self.assertRaises(PermissionError) as caught:
                space.cleanup()
            self.assertIn(caught.exception.winerror,(5,32))
            self.assertTrue(path.is_dir())
        finally:
            release.touch();child.wait(timeout=5)
            space.cleanup()
        self.assertFalse(path.exists())

    def test_unconfirmed_owned_processes_retain_the_workspace_and_raise(self):
        space=CLIWorkspace()
        path=Path(space.name).resolve()
        try:
            space.processes_stopped=False
            with self.assertRaises(ContainmentError):space.cleanup()
            self.assertTrue(path.is_dir())
        finally:
            space.processes_stopped=True
            space.cleanup()

    def test_failed_launch_cleans_only_after_the_empty_owned_tree_is_confirmed(self):
        made=[]
        original=tempfile.mkdtemp
        def make(*args,**kwargs):
            kwargs['dir']=str(self.base)
            if len(args)>2:args=(*args[:2],str(self.base),*args[3:]);kwargs.pop('dir')
            path=original(*args,**kwargs);made.append(Path(path).resolve());return path
        with patch('execution.generation.tempfile.mkdtemp',side_effect=make), \
             patch.object(worker._WindowsProcessTree,'launch',side_effect=OSError('fixture launch failure')):
            with self.assertRaises(BoundaryError) as caught:
                transport({'limits':{'max_saved_response_bytes':32,'call_timeout_seconds':2},
                    'prompt':'fixed fixture'},[sys.executable,'-B','-c',"print('not started')"])
        self.assertEqual(caught.exception.code,'launch_failed')
        self.assertEqual(len(made),1)
        self.assertFalse(made[0].exists())
