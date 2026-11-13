"""Default CLI uses bounded no-follow choices without changing legacy storage."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine import calendar_fs
from execution.calendar_native import MAX_CHOICES_BYTES
from scripts import calendar_sim

ROOT=Path(__file__).resolve().parents[1]


class LegacyCLIChoicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wb-legacy-choices-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.inputs=self.root/'inputs'
        shutil.copytree(ROOT/'projects/calendar_workshop_example',self.inputs)
        self.world=self.inputs/'world.yaml';self.choices=self.inputs/'choices.json';self.output=self.root/'output'

    def invoke(self,*extra):
        out,err=io.StringIO(),io.StringIO()
        with patch.object(sys,'argv',['calendar_sim.py','--world',str(self.world),'--choices',str(self.choices),
                '--out',str(self.output),*map(str,extra)]),redirect_stdout(out),redirect_stderr(err):
            code=calendar_sim.main()
        return code,out.getvalue(),err.getvalue()

    def rejected(self):
        with patch.object(calendar_sim,'load_project',side_effect=AssertionError('must reject before loading/running')):
            code,out,err=self.invoke()
        self.assertEqual(code,2);self.assertEqual(out,'')
        self.assertEqual(json.loads(err)['status'],'invalid_config_or_resume')
        self.assertFalse(self.output.exists())

    def test_default_and_explicit_legacy_keep_existing_outputs_and_noop_resume(self):
        code,out,err=self.invoke();self.assertEqual(code,0,err)
        self.assertEqual(json.loads(out)['status'],'completed')
        self.assertTrue((self.output/'calendar-state.json').is_file())
        self.assertFalse(list(self.output.glob('wbgen-*')))
        before={name:(self.output/name).read_bytes() for name in ('layers.jsonl','summary.json','calendar-state.json','run-record.json')}
        code,out,err=self.invoke('--storage','legacy','--resume');self.assertEqual(code,0,err)
        self.assertEqual(before,{name:(self.output/name).read_bytes() for name in before})
        self.assertEqual(json.loads(before['summary.json'])['tasks']['pieces']['units'],3)

    def test_exact_bound_regular_json_passes_and_one_over_rejects(self):
        self.choices.write_bytes(b'[]'+b' '*(MAX_CHOICES_BYTES-2))
        code,_out,err=self.invoke();self.assertEqual(code,0,err)
        shutil.rmtree(self.output)
        self.choices.write_bytes(b'[]'+b' '*(MAX_CHOICES_BYTES-1));self.rejected()

    def test_no_choices_path_stat_or_read_text_reopen(self):
        original_stat,original_read=Path.stat,Path.read_text
        def stating(path,*args,**kwargs):
            if path==self.choices:raise AssertionError('choices path stat race')
            return original_stat(path,*args,**kwargs)
        def reading(path,*args,**kwargs):
            if path==self.choices:raise AssertionError('choices path read reopen')
            return original_read(path,*args,**kwargs)
        with patch.object(Path,'stat',stating),patch.object(Path,'read_text',reading):
            code,_out,err=self.invoke()
        self.assertEqual(code,0,err)

    def test_invalid_json_duplicate_nonfinite_utf8_and_nonarray_reject_before_run(self):
        for data in (b'[',b'[{"id":1,"id":2}]',b'[NaN]',b'\xff',b'{}'):
            with self.subTest(data=data):self.choices.write_bytes(data);self.rejected()

    def test_directory_is_not_choices(self):
        self.choices.unlink();self.choices.mkdir();self.rejected()

    def test_hardlinked_choices_rejected(self):
        os.link(self.choices,self.inputs/'linked-copy.json');self.rejected()

    @unittest.skipUnless(os.name=='posix','POSIX special-file fixture')
    def test_fifo_rejected_without_waiting_for_writer(self):
        self.choices.unlink();os.mkfifo(self.choices);self.rejected()

    @unittest.skipUnless(os.name=='posix','POSIX symlink fixture')
    def test_choices_leaf_and_ancestor_links_rejected(self):
        original=self.inputs/'original-choices.json';self.choices.rename(original)
        self.choices.symlink_to(original);self.rejected();self.choices.unlink();original.rename(self.choices)
        alias=self.root/'alias-inputs';alias.symlink_to(self.inputs,target_is_directory=True)
        self.choices=alias/'choices.json';self.rejected()

    @unittest.skipUnless(os.name=='posix','POSIX deterministic open fixture')
    def test_replaced_before_open_oversize_rejected_without_unbounded_read(self):
        real=calendar_fs._open;done=False
        def opening(name,flags,**kwargs):
            nonlocal done
            if name=='choices.json' and not done:
                done=True;self.choices.write_bytes(b' '*(MAX_CHOICES_BYTES+1))
            return real(name,flags,**kwargs)
        with patch.object(calendar_fs,'_open',opening):self.rejected()

    @unittest.skipUnless(os.name=='posix','POSIX retained-inode replacement fixture')
    def test_replaced_label_after_open_never_changes_captured_commands(self):
        real=calendar_fs._open;done=False
        def opening(name,flags,**kwargs):
            nonlocal done
            fd=real(name,flags,**kwargs)
            if name=='choices.json' and not done:
                done=True;self.choices.rename(self.inputs/'retained-choices.json');self.choices.write_text('[]')
            return fd
        with patch.object(calendar_fs,'_open',opening):code,_out,err=self.invoke()
        self.assertEqual(code,0,err)
        self.assertEqual(json.loads((self.output/'summary.json').read_text())['tasks']['pieces']['units'],3)

    @unittest.skipUnless(os.name=='posix','POSIX retained file mutation fixture')
    def test_inplace_mutation_during_choices_read_rejected(self):
        real=os.read;changed=False
        def reading(fd,size):
            nonlocal changed
            data=real(fd,size)
            if not changed:changed=True;self.choices.write_text('[]')
            return data
        with patch('engine.calendar_fs.os.read',reading):self.rejected()

    def test_closed_source_failure_prevents_legacy_execution(self):
        from execution import calendar_native
        real=calendar_native._owned_directory
        from contextlib import contextmanager
        @contextmanager
        def failing(path):
            with real(path) as directory:yield directory
            raise OSError('source close failed')
        with patch.object(calendar_native,'_owned_directory',failing):self.rejected()


if __name__=='__main__':unittest.main()
