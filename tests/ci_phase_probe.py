"""Opt-in CI diagnostics for three existing tests; no payload or path logging."""
import atexit
import builtins
from contextlib import contextmanager
import functools
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import uuid

_tls = threading.local()
_queue = queue.Queue(maxsize=100000)
_counter = 0
_dropped = 0
_original_open = io.open
_original_import = builtins.__import__
_lock_ids = {}
_request_cases = {}
_installed = False
_creation = time.time_ns()
_observer_errors = 0
PROBE_VERSION = '4-ci'
TARGETS = frozenset((
    'test_evolution_execution.EvolutionHttpPersonalityGrowthTests.test_personality_growth_true_completes_and_writes_growth_rows',
    'test_evolution_execution.EvolutionHttpRouteRhoTests.test_route_rho_from_job_reaches_the_ga',
    'test_genre_workspace.GenreWorkspaceTests.test_draft_check_uses_unsaved_selected_genre_and_never_saves',
))
PROGRESS = frozenset(('completed_generations','total_generations','completed_individuals',
                     'total_individuals','completed_seeds','total_seeds'))


def active():
    case=getattr(_tls,'case',None)
    return bool(getattr(_tls,'span',None) or (case and case[0] in TARGETS) or os.environ.get('WB_PHASE_TARGET_ACTIVE') == '1')


def _number(value):
    if type(value) in (int,bool): return value
    return value if type(value) is float and math.isfinite(value) else None


def _json_safe(value):
    if value is None or type(value) in (str,int,bool): return value
    if type(value) is float: return value if math.isfinite(value) else None
    if type(value) in (list,tuple): return [_json_safe(v) for v in value]
    if type(value) is dict: return {k:_json_safe(v) for k,v in value.items() if type(k) is str}
    return None  # Never invoke str/repr on an arbitrary observed object.


def _test_id(value):
    return value if type(value) is str and len(value)<=256 and re.fullmatch(r'test_[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.test_[A-Za-z0-9_]+',value) else '<unknown>'


def _request_id(value):
    return value if type(value) is str and re.fullmatch(r'[0-9a-f]{32}',value) else ''


def emit(event, **fields):
    global _dropped
    case=getattr(_tls,'case',None)
    test,occurrence=case if case else (os.environ.get('WB_PHASE_TEST',''),os.environ.get('WB_PHASE_OCCURRENCE','0'))
    row = dict(event=event, pid=os.getpid(), process_created=_creation,
               tid=threading.get_ident(), mono=time.monotonic(), wall=time.time(),
               test=_test_id(test),
               occurrence=int(occurrence) if str(occurrence).isdigit() else 0,
               request=_request_id(getattr(_tls, 'request', os.environ.get('WB_PHASE_REQUEST', ''))),
               **_json_safe(fields))
    try:
        _queue.put_nowait(row)
    except queue.Full:
        _dropped += 1


def _writer():
    global _observer_errors
    root = Path(os.environ['WB_PHASE_TRACE'])
    root.mkdir(parents=True, exist_ok=True)
    with _original_open(root / (str(os.getpid()) + '-' + str(_creation) + '.jsonl'), 'a', encoding='utf-8') as stream:
        last = time.monotonic()
        while True:
            try:
                row = _queue.get(timeout=.25)
            except queue.Empty:
                stream.flush()
                last = time.monotonic()
                continue
            if isinstance(row, threading.Event):
                stream.flush()
                row.set()
                continue
            if row is None:
                stream.flush()
                return
            stream.write(json.dumps(row, separators=(',', ':'),allow_nan=False) + '\n')
            if time.monotonic() - last >= .25:
                stream.flush()
                last = time.monotonic()


def flush():
    done = threading.Event()
    try: _queue.put_nowait(done)
    except queue.Full: return False
    return done.wait(2)


def finish():
    emit('observer.stop', dropped=_dropped, observer_errors=_observer_errors)
    flush()
    try: _queue.put_nowait(None)
    except queue.Full: pass


@contextmanager
def phase(name, **fields):
    global _counter
    _counter += 1
    span = f'{os.getpid()}-{_counter}'
    previous = getattr(_tls, 'span', None)
    previous_case = getattr(_tls,'case',None)
    if previous_case is None:
        _tls.case=(os.environ.get('WB_PHASE_TEST',''),os.environ.get('WB_PHASE_OCCURRENCE','0'))
    previous_stats = getattr(_tls, 'stats', None)
    _tls.stats = {}
    _tls.span = span
    start = time.monotonic()
    cpu = os.times()
    emit('begin', span=span, parent=previous, phase=name, **fields)
    error = None
    error_fields = {}
    try:
        yield
    except BaseException as exc:
        error = type(exc).__name__
        error_fields = {'errno':getattr(exc,'errno',None),'winerror':getattr(exc,'winerror',None)}
        raise
    finally:
        after = os.times()
        emit('end', span=span, phase=name, seconds=time.monotonic()-start,
             cpu_seconds=(after.user+after.system)-(cpu.user+cpu.system), error=error,
             io_counters=_tls.stats, **error_fields)
        _tls.span = previous
        _tls.case = previous_case
        _tls.stats = previous_stats


def _safe_id(value):
    return value if type(value) is str and re.fullmatch(r'job-[0-9a-f]{64}', value) else None


def _native_request_hash(value):
    return hashlib.sha256(value.encode()).hexdigest() if type(value) is str and len(value)<=128 else None


def _wrap(owner, name, label=None):
    original = getattr(owner, name, None)
    if not callable(original) or getattr(original, '_wb_phase_wrapped', False):
        return
    label = label or getattr(owner, '__name__', type(owner).__name__) + '.' + name
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        http_case=None
        if name in ('do_POST','do_GET'):
            try:http_case=_request_cases.get(_request_id(args[0].headers.get('X-WB-Phase-Request','')))
            except (AttributeError,TypeError,IndexError):pass
        if not active() and not (http_case and http_case[0] in TARGETS):return original(*args,**kwargs)
        previous = getattr(_tls, 'request', None)
        previous_case=getattr(_tls,'case',None)
        if name in ('do_POST', 'do_GET'):
            _tls.request = _request_id(args[0].headers.get('X-WB-Phase-Request', ''))
            if http_case:_tls.case=http_case
        fields = {}
        if name in ('prepare_run', 'verify_run'):
            fields['job'] = _safe_id(kwargs.get('job_id'))
        if name in ('main', 'prepare') and args and hasattr(args[0], 'job'):
            fields['job'] = _safe_id(args[0].job)
        if name == 'materialize' and len(args) >= 2:
            fields.update(files=len(args[1]), bytes=sum(map(len, args[1].values())))
        if name in ('materialize','verify_files') and args:
            category=Path(args[0]).name
            fields['scope']=category if category in ('inputs','runtime','repo') else 'temporary'
        if (name in ('atomic','atomic_json') or label=='worker.read') and args:
            category=Path(args[0]).name
            fields['record']=category if category in ('job.json','request.json','prepared.json','config.json','input-manifest.json','runtime-manifest.json','manifest.json','complete.json') else 'other'
        if name == 'verify_files' and len(args) >= 2:
            fields['files'] = len(args[1])
        if name == 'run' and args and isinstance(args[0], (list, tuple)):
            executable = Path(str(args[0][0])).name
            fields['executable'] = executable if executable in ('git','git.exe','python','python.exe') else 'other'
        if name == 'launch' and len(args)>=3:
            fields['cwd_length'] = len(str(args[2]))
        if name in ('write', 'write_bytes') and len(args) >= 2:
            try: fields['bytes'] = len(args[1])
            except TypeError: pass
        try:
            with phase(label, **fields):
                result = original(*args, **kwargs)
                if name == 'submit' and isinstance(result, dict):
                    emit('job.correlate', job=_safe_id(result.get('job_id')),
                         native_request_sha256=_native_request_hash(args[1].get('request_id')) if len(args)>1 and type(args[1]) is dict else None)
                if name == 'change' and isinstance(result,dict):
                    progress=result.get('progress')
                    emit('job.state',job=_safe_id(result.get('job_id')),
                         state=result.get('state') if result.get('state') in ('queued','running','stopping','failed','succeeded','partial','cancelled','interrupted') else None,
                         phase=result.get('phase') if result.get('phase') in ('preparing','evaluating','finishing','generating','evaluating_independent') else None,
                         heartbeat=_number(result.get('heartbeat')),revision=_number(result.get('revision')),
                         progress={k:_number(progress[k]) for k in PROGRESS if type(progress) is dict and k in progress})
                if name in ('launch', 'process_identity'):
                    ident = result if isinstance(result, dict) else {'pid': getattr(result, 'pid', None)}
                    emit('process.identity', phase=label, child_pid=ident.get('pid'), child_created=ident.get('created'))
                return result
        finally:
            if name in ('do_POST', 'do_GET'):
                _tls.request = previous
                _tls.case = previous_case
    wrapped._wb_phase_wrapped = True
    setattr(owner, name, wrapped)


class _WriteStream:
    def __init__(self, stream): self.stream = stream
    def __getattr__(self, name): return getattr(self.stream, name)
    def __enter__(self): self.stream.__enter__(); return self
    def __exit__(self, *args):
        with phase('file.close'): return self.stream.__exit__(*args)
    def write(self, data):
        with phase('file.write', bytes=len(data)): return self.stream.write(data)
    def flush(self):
        with phase('file.flush'): return self.stream.flush()


def _open(*args, **kwargs):
    stream = _original_open(*args, **kwargs)
    mode = args[1] if len(args)>1 else kwargs.get('mode', 'r')
    if getattr(_tls, 'span', None) and 'b' in mode and any(c in mode for c in 'wax'):
        return _WriteStream(stream)
    return stream


def _patch_module(module):
    name = module.__name__
    if name=='yaml':
        for key in ('safe_load','safe_dump'):
            _wrap(module,key,'yaml.'+key)
    elif name.endswith('provenance'):
        for key in ('write_bytes','atomic_json','materialize','verify_files','code_snapshot','publish_directory'):
            _wrap(module, key, 'provenance.'+key)
    elif name.endswith('configs'):
        for key in ('_capture_inputs','_describe'):
            _wrap(module, key, 'configs.'+key)
        cls = getattr(module, 'ConfigStore', None)
        if cls:
            for key in ('_prepare','preview','save','_bundle','prepare_run','verify_run'):
                _wrap(cls, key, 'ConfigStore.'+key)
    elif name.endswith('genre_editor'):
        for key in ('validate_draft','snapshot','parse'):
            _wrap(module, key, 'genre.'+key)
    elif name.endswith('worker') or (name=='__main__' and hasattr(module, 'trace_lock')):
        for key in ('prepare','watch','atomic','read','replace_job_snapshot','process_identity','change'):
            _wrap(module, key, 'worker.'+key)
        for cls_name in ('_WindowsProcessTree','_LinuxProcessTree'):
            cls = getattr(module, cls_name, None)
            if cls: _wrap(cls, 'launch', 'process.launch')
        old = getattr(module, 'trace_lock', None)
        if old and not getattr(old, '_wb_phase_wrapped', False):
            @functools.wraps(old)
            def traced(folder, operation, stage, **values):
                if not active(): return old(folder,operation,stage,**values)
                key = str(folder)
                if key not in _lock_ids:
                    _lock_ids[key] = hashlib.sha256(os.path.normcase(os.path.realpath(key)).encode()).hexdigest()
                numeric = {k:_number(v) for k,v in values.items() if k in ('wait_seconds','held_seconds','seconds','errno','winerror')}
                allowed_ops=('jobs','jobs.get','worker.lock','worker.read_job','worker.change','directory_lock')
                allowed_stages=('thread_acquired','acquired','released','contended','timeout','snapshot','read_error','read','write','event')
                emit('lock', lock_id=_lock_ids[key],operation=operation if operation in allowed_ops else 'other',
                     stage=stage if stage in allowed_stages else 'other',
                     observation=values.get('observation') if values.get('observation') in ('alive','dead','unknown','terminal','expired','unconfirmed') else None,**numeric)
                with phase('legacy.trace_lock'):
                    return old(folder, operation, stage, **values)
            traced._wb_phase_wrapped = True
            module.trace_lock = traced
    elif name.endswith('jobs'):
        cls = getattr(module, 'JobStore', None)
        if cls: _wrap(cls, 'submit', 'JobStore.submit')
    elif name=='viewer.server':
        cls = getattr(module,'ViewerHandler',None)
        if cls:
            for key in ('do_POST','do_GET','_request_json','_send_json','_send_bytes','send_response','end_headers','log_message'):
                _wrap(cls,key,'http.server.'+key)


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if active() and name in ('execution.configs','yaml') and name not in sys.modules:
        with phase('import.'+name):
            result = _original_import(name, globals, locals, fromlist, level)
    else:
        result = _original_import(name, globals, locals, fromlist, level)
    if not getattr(_tls, 'patching', False) and (name.startswith(('execution','viewer')) or (globals or {}).get('__name__','').startswith(('execution','viewer'))):
        _tls.patching = True
        try:
            for key in ('execution.provenance','execution.configs','execution.genre_editor','execution.jobs','execution.worker','viewer.server','yaml'):
                module = sys.modules.get(key)
                if module and not getattr(getattr(module,'__spec__',None),'_initializing',False):
                    _patch_module(module)
        finally:
            _tls.patching = False
    return result


def _measure(owner,name,label):
    original=getattr(owner,name)
    @functools.wraps(original)
    def measured(*args,**kwargs):
        stats=getattr(_tls,'stats',None)
        if stats is None:return original(*args,**kwargs)
        start=time.monotonic()
        size=0
        try:
            result=original(*args,**kwargs)
            if name=='read_bytes' and type(result) is bytes:size=len(result)
            return result
        finally:
            value=stats.setdefault(label,{'count':0,'seconds':0.,'max_seconds':0.,'bytes':0})
            elapsed=time.monotonic()-start
            value['count']+=1
            value['seconds']+=elapsed
            value['max_seconds']=max(value['max_seconds'],elapsed)
            value['bytes']+=size
    setattr(owner,name,measured)


def install(main_module=None):
    global _installed, _creation
    if _installed:
        if main_module: _patch_module(main_module)
        return
    _installed = True
    # Best-effort numeric OS process identity, without path or command line.
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL('kernel32',use_last_error=True)
        api.GetCurrentProcess.restype=w.HANDLE
        api.GetProcessTimes.argtypes=[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4
        ts=[w.FILETIME() for _ in range(4)]
        if api.GetProcessTimes(api.GetCurrentProcess(),*(ctypes.byref(t) for t in ts)):
            _creation=(ts[0].dwHighDateTime<<32)|ts[0].dwLowDateTime
    threading.Thread(target=_writer,daemon=True,name='wb-phase-writer').start()
    atexit.register(finish)
    builtins.__import__ = _import
    io.open = _open
    for key in ('fsync','replace','rename'): _wrap(os,key,'os.'+key)
    for key in ('stat','lstat'): _measure(os,key,'os.'+key)
    for key in ('resolve','read_bytes'): _measure(Path,key,'path.'+key)
    import subprocess, tempfile, shutil, http.client, socketserver
    _wrap(subprocess,'run','subprocess.run')
    _wrap(tempfile.TemporaryDirectory,'cleanup','temp.cleanup')
    _wrap(shutil,'rmtree','temp.rmtree')
    _wrap(socketserver._SocketWriter,'write','http.server.body_write')
    for key in ('request','getresponse'):
        _wrap(http.client.HTTPConnection,key,'http.client.'+key)
    _wrap(http.client.HTTPResponse,'read','http.client.body_read')
    original_request=http.client.HTTPConnection.request
    missing=object()
    @functools.wraps(original_request)
    def request(self,method,url,body=None,headers=missing,**kwargs):
        if not active():
            if headers is missing:return original_request(self,method,url,body,**kwargs)
            return original_request(self,method,url,body,headers,**kwargs)
        headers={} if headers is missing else dict(headers)
        rid=uuid.uuid4().hex
        _request_cases[rid]=getattr(_tls,'case',None) or (os.environ.get('WB_PHASE_TEST',''),os.environ.get('WB_PHASE_OCCURRENCE','0'))
        if len(_request_cases)>8192:_request_cases.pop(next(iter(_request_cases)),None)
        headers['X-WB-Phase-Request']=rid
        _tls.request=rid
        os.environ['WB_PHASE_REQUEST']=rid
        self._wb_phase_request=rid
        return original_request(self,method,url,body,headers,**kwargs)
    http.client.HTTPConnection.request=request
    for module in list(sys.modules.values()):
        if module and getattr(module,'__name__','') in ('execution.provenance','execution.configs','execution.genre_editor','execution.jobs','execution.worker','viewer.server','yaml'):
            _patch_module(module)
    if main_module: _patch_module(main_module)
    entry=Path(sys.argv[0]).name
    if entry not in ('worker.py','evolution_worker.py','evolve.py','ci_phase_suite.py','regression_partition.py'):entry='other'
    role='preparation' if '--prepare' in sys.argv else 'supervisor' if entry=='worker.py' else 'ga' if entry in ('evolution_worker.py','evolve.py') else 'runner'
    emit('observer.start', legacy_diagnostics=os.environ.get('WB_REGRESSION_DIAGNOSTICS','0'),
         probe_version=PROBE_VERSION,role=role,entry=entry,python=sys.version.split()[0])
