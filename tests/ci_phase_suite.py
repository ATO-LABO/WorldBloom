"""Run the original census in an opt-in diagnostic worktree, without changing deadlines."""
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
BOOTSTRAP='''
# Diagnostic worktree only; original checkout and release sources stay intact.
if os.environ.get("WB_PHASE_TRACE") and os.environ.get("WB_PHASE_TARGET_ACTIVE") == "1":
    import importlib.util as _wb_importlib
    if "ci_phase_probe" not in sys.modules:
        _wb_spec = _wb_importlib.spec_from_file_location("ci_phase_probe", os.environ["WB_PHASE_HELPER"])
        _wb_module = _wb_importlib.module_from_spec(_wb_spec)
        sys.modules["ci_phase_probe"] = _wb_module
        _wb_spec.loader.exec_module(_wb_module)
    sys.modules["ci_phase_probe"].install(sys.modules[__name__])

'''


def git(*args):
    p=subprocess.run(['git','-C',str(ROOT),*args],capture_output=True,text=True,encoding='utf-8',
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if p.returncode: raise RuntimeError('Diagnostic git operation failed')
    return p.stdout.strip()


def capture_failure(probe,test,kind):
    """Owned temporary job metadata only, before unittest's original cleanup."""
    probe.emit('test.failure_before_cleanup',kind=kind)
    root=getattr(test,'base',None)
    snapshots=[]
    if root is not None:
        from execution import worker
        from execution.provenance import contained, read_atomic_json
        try:
            base=Path(root).absolute()
            temporary=Path(tempfile.gettempdir()).resolve()
            if not base.resolve().is_relative_to(temporary): raise ValueError('outside-owned-temp')
            jobs=contained(base,'control/jobs')
            for path in jobs.glob('job-*/job.json') if jobs.exists() else ():
                path=contained(base,path.relative_to(base).as_posix())
                if path.stat().st_size>2*1024*1024: raise ValueError('oversized-job')
                raw=read_atomic_json(path)
                safe={'job_id':probe._safe_id(raw.get('job_id'))}
                safe['state']=raw.get('state') if raw.get('state') in worker.TERMINAL|{'queued','running','stopping'} else None
                safe['phase']=raw.get('phase') if raw.get('phase') in ('preparing','evaluating','finishing','generating','evaluating_independent') else None
                safe.update({k:probe._number(raw.get(k)) for k in ('heartbeat','created_at','started_at','finished_at','revision')})
                safe['identities']={}
                identities={k:raw.get(k) for k in ('launch_identity','preparation_identity','child_identity')}
                identities['supervisor']=(raw.get('receipt') or {}).get('identity')
                for name,identity in identities.items():
                    if type(identity) is dict:
                        safe['identities'][name]={'pid':probe._number(identity.get('pid')),
                            'created':probe._number(identity.get('created')),'probe':worker.probe(identity)}
                snapshots.append(safe)
        except Exception as error:
            probe.emit('snapshot.unavailable',error=type(error).__name__)
    probe.emit('failure.owned_jobs',jobs=snapshots)
    probe.flush()


def result_class(original,probe,owned_events=None):
    class DiagnosticResult(original):
        def __init__(self,*args,**kwargs):
            # The census's own tests also instantiate RecordingResult with a
            # StringIO to exercise an intentional failure. Observe only the
            # real suite's event stream; leave those nested fixtures original.
            self.phase_enabled=(owned_events is None or
                getattr(kwargs.get('events'),'name',None)==str(owned_events))
            super().__init__(*args,**kwargs)
            self.phase_counts=Counter()
        def startTest(self,test):
            if not self.phase_enabled:return super().startTest(test)
            ident=test.id()
            self.phase_counts[ident]+=1
            os.environ.update(WB_PHASE_TEST=ident,WB_PHASE_OCCURRENCE=str(self.phase_counts[ident]),
                              WB_PHASE_TARGET_ACTIVE='1' if ident in probe.TARGETS else '0')
            self.phase_started=time.monotonic()
            probe.emit('test.begin')
            super().startTest(test)
        def stopTest(self,test):
            if not self.phase_enabled:return super().stopTest(test)
            probe.emit('test.end',seconds=time.monotonic()-self.phase_started)
            super().stopTest(test)
            os.environ['WB_PHASE_TARGET_ACTIVE']='0'
        def addFailure(self,test,err):
            if not self.phase_enabled:return super().addFailure(test,err)
            capture_failure(probe,test,'failure')
            super().addFailure(test,err)
        def addError(self,test,err):
            if not self.phase_enabled:return super().addError(test,err)
            capture_failure(probe,test,'error')
            super().addError(test,err)
        def addSubTest(self,test,subtest,err):
            if not self.phase_enabled:return super().addSubTest(test,subtest,err)
            if err is not None: capture_failure(probe,test,'subtest_failure')
            super().addSubTest(test,subtest,err)
    return DiagnosticResult


def main():
    evidence=Path(os.environ['WB_REGRESSION_EVIDENCE_DIR']).resolve()
    evidence.mkdir(parents=True,exist_ok=True)
    head=git('rev-parse','HEAD')
    expected=os.environ.get('WB_SOURCE_HEAD',head)
    # PR checkout retains GitHub's merge commit, as the original CI did. Keep
    # the review head and the actual integrated input separately identifiable.
    git('merge-base','--is-ancestor',expected,head)
    local_smoke='--local-target-smoke' in sys.argv
    if local_smoke:
        if os.environ.get('GITHUB_ACTIONS')=='true' or os.environ.get('CI')=='true':
            raise ValueError('Local target smoke cannot replace a CI census')
        sys.argv.remove('--local-target-smoke')
    source_parent=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir())).resolve()
    source=source_parent/('wb-phase-source-'+uuid.uuid4().hex)
    assert source.parent==source_parent and not source.exists()
    nonce=uuid.uuid4().hex
    git('worktree','add','--detach',str(source),head)
    (source/'.ci-phase-owned').write_text(nonce,encoding='ascii')
    original=(source/'execution/worker.py').read_bytes()
    marker='if __name__ == "__main__":'
    text=original.decode('utf-8')
    assert text.count(marker)==1
    (source/'execution/worker.py').write_text(text.replace(marker,BOOTSTRAP+marker),encoding='utf-8',newline='\n')
    from difflib import unified_diff
    patch=''.join(unified_diff(text.splitlines(True),(source/'execution/worker.py').read_text(encoding='utf-8').splitlines(True),
                              fromfile='a/execution/worker.py',tofile='b/execution/worker.py'))
    (evidence/'diagnostic-replica.patch').write_text(patch,encoding='utf-8')
    metadata={'input_head':head,'review_head':expected,'input_tree':git('rev-parse',head+'^{tree}'),
              'review_tree':git('rev-parse',expected+'^{tree}'),
              'probe_sha256':hashlib.sha256((ROOT/'tests/ci_phase_probe.py').read_bytes()).hexdigest(),
              'original_worker_sha256':hashlib.sha256(original).hexdigest(),
              'diagnostic_worker_sha256':hashlib.sha256((source/'execution/worker.py').read_bytes()).hexdigest(),
              'instrumented_tests':[],
              'diagnostic_copy_only':True,'product_checkout_modified':False}
    os.environ.update(WB_PHASE_TRACE=str(evidence/'phases'),WB_PHASE_HELPER=str(ROOT/'tests/ci_phase_probe.py'),
                      WB_PHASE_TARGET_ACTIVE='0')
    for key,suffix in (('MELOS_APPROVED_ADAPTER_DIR','execution/approved_melos_sources'),('MELOS_INPUT_DIR','projects/melos-input')):
        os.environ[key]=str(source/suffix)
    spec=importlib.util.spec_from_file_location('ci_phase_probe',ROOT/'tests/ci_phase_probe.py')
    probe=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=probe
    spec.loader.exec_module(probe)
    probe.install()
    metadata['instrumented_tests']=sorted(probe.TARGETS)
    (evidence/'phase-input.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    previous=Path.cwd()
    os.chdir(source)
    sys.path[:0]=[str(source/'tests'),str(source)]
    import regression_partition
    regression_partition.RecordingResult=result_class(regression_partition.RecordingResult,probe,
        evidence/('local-smoke-events.jsonl' if local_smoke else 'test-events.jsonl'))
    outcome=1
    try:
        if local_smoke:
            import unittest
            with (evidence/'local-smoke-events.jsonl').open('w',encoding='utf-8') as events:
                result=unittest.TextTestRunner(verbosity=2,resultclass=lambda *a,**kw:
                    regression_partition.RecordingResult(*a,events=events,**kw)).run(
                    unittest.defaultTestLoader.loadTestsFromNames(sorted(probe.TARGETS)))
            outcome=0 if result.wasSuccessful() else 1
            (evidence/'local-smoke.json').write_text(json.dumps({'full_census':False,'tests_run':result.testsRun,
                'failures':len(result.failures),'errors':len(result.errors),'success':result.wasSuccessful()},indent=2),encoding='utf-8')
        else:
            outcome=regression_partition.main()
        os.environ['WB_PHASE_TARGET_ACTIVE']='0'
        healthy=probe.flush() and probe._dropped==0 and probe._observer_errors==0
        changed=subprocess.run(['git','-C',str(source),'diff','--name-only'],capture_output=True,text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        source_ok=(changed.returncode==0 and changed.stdout.splitlines()==['execution/worker.py'] and
                   hashlib.sha256((ROOT/'execution/worker.py').read_bytes()).hexdigest()==metadata['original_worker_sha256'] and
                   hashlib.sha256((source/'execution/worker.py').read_bytes()).hexdigest()==metadata['diagnostic_worker_sha256'])
        healthy=healthy and source_ok
        with (evidence/'phase-summary.json').open('w',encoding='utf-8') as summary:
            analyzed=subprocess.run([sys.executable,'-B',str(ROOT/'tests/ci_phase_analyze.py'),str(evidence)],
                                    stdout=summary,stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        healthy=healthy and analyzed.returncode==0
        (evidence/'phase-health.json').write_text(json.dumps({'healthy':healthy,'dropped':probe._dropped,
            'observer_errors':probe._observer_errors,'regression_exit_code':outcome,
            'only_diagnostic_worker_changed':source_ok},indent=2),encoding='utf-8')
        return outcome if outcome else (0 if healthy else 1)
    finally:
        os.environ['WB_PHASE_TARGET_ACTIVE']='0'
        os.chdir(previous)
        # A failing suite keeps its owned source for inspection. Never clean an
        # unrelated checkout, unknown path or existing user's working directory.
        if outcome==0 and source.resolve().parent==source_parent and source.name.startswith('wb-phase-source-'):
            if (source/'.ci-phase-owned').read_text(encoding='ascii')==nonce:
                git('worktree','remove','--force',str(source))


if __name__=='__main__':raise SystemExit(main())
