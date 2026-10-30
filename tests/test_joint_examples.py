"""Authored examples must run from the shipped copy and preserve old evidence."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_joint_examples as examples

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('examples_launcher',ROOT/'tools/local_launcher.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class JointExampleTests(unittest.TestCase):
    def test_repeated_cli_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'evidence';saved=out/'voyage/layers.jsonl'
            saved.parent.mkdir(parents=True);saved.write_bytes(b'preserve saved evidence\n')
            with patch.object(sys,'argv',['run_joint_examples','--out',str(out),'--example','voyage']),redirect_stdout(io.StringIO()):
                with self.assertRaises(FileExistsError):examples.main()
            self.assertEqual(saved.read_bytes(),b'preserve saved evidence\n')
            self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()),['voyage/layers.jsonl'])

    def test_direct_runner_preserves_existing_trial(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'trial';out.mkdir();saved=out/'joint-report.json';saved.write_bytes(b'old report\n')
            with self.assertRaises(FileExistsError):examples.run(ROOT/'projects/joint_voyage_example',out)
            self.assertEqual(saved.read_bytes(),b'old report\n')
            self.assertFalse((out/'layers.jsonl').exists())

    def test_packaged_examples_run_without_the_source_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);workspace=launcher.prepare(ROOT,root/'state')
            checked=subprocess.run([sys.executable,'-B','scripts/run_joint_examples.py','--out',str(root/'results')],
                cwd=workspace,env=dict(os.environ,PYTHONPATH=str(workspace),PYTHONNOUSERSITE='1'),
                capture_output=True,text=True,encoding='utf8',timeout=60)
            self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
            reports=json.loads(checked.stdout)
            self.assertEqual({r['project'] for r in reports},{'joint_voyage_example','joint_repair_example','joint_festival_example'})
            for report in reports:
                self.assertEqual(report['api_calls'],0)
                self.assertTrue(report['segments'])
                self.assertTrue(all(s['status']=='completed' for s in report['segments'].values()))
                self.assertTrue(Path(report['layers']).is_file())
