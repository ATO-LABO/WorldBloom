"""Release a per-call CLI directory only after owned processes and handles stop."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time


def directory_holders(path):
    """Read holders of this directory only; never enumerate or kill unrelated processes."""
    if os.name != 'nt':
        return {}
    import ctypes
    from ctypes import wintypes as wt
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wt.LPCWSTR,wt.DWORD,wt.DWORD,ctypes.c_void_p,wt.DWORD,wt.DWORD,wt.HANDLE]
    api.CreateFileW.restype=wt.HANDLE
    api.CloseHandle.argtypes=[wt.HANDLE]
    api.CloseHandle.restype=wt.BOOL
    handle=api.CreateFileW(str(path),0x80,7,None,3,0x02000000,None)
    if handle==wt.HANDLE(-1).value:
        return {'holder_query_winerror':ctypes.get_last_error()}
    class Status(ctypes.Structure):
        _fields_=[('status',ctypes.c_void_p),('information',ctypes.c_size_t)]
    class Info(ctypes.Structure):
        _fields_=[('count',wt.ULONG),('pids',ctypes.c_size_t*256)]
    query=ctypes.WinDLL('ntdll').NtQueryInformationFile
    query.argtypes=[wt.HANDLE,ctypes.POINTER(Status),ctypes.c_void_p,wt.ULONG,ctypes.c_int]
    query.restype=ctypes.c_long
    status=Status();info=Info()
    try:
        code=query(handle,ctypes.byref(status),ctypes.byref(info),ctypes.sizeof(info),47)
        return {'holder_query_status':code & 0xffffffff,
                'holder_pids':list(info.pids[:min(info.count,256)]) if code==0 else []}
    finally:
        api.CloseHandle(handle)


def trace_workspace(path, stage, **values):
    evidence=os.environ.get('WB_REGRESSION_EVIDENCE_DIR')
    if not evidence or os.environ.get('WB_REGRESSION_DIAGNOSTICS')!='1':
        return
    try:
        root=Path(evidence)/'cli-workspaces'
        root.mkdir(parents=True,exist_ok=True)
        row={'time':time.time(),'pid':os.getpid(),'stage':stage,
             'workspace_id':hashlib.sha256(os.path.normcase(os.path.realpath(path)).encode()).hexdigest(),**values}
        with (root/(str(os.getpid())+'.jsonl')).open('a',encoding='utf-8') as log:
            log.write(json.dumps(row,sort_keys=True)+'\n')
    except (OSError,TypeError,ValueError):
        # A test/proxy may expose non-JSON metadata. Diagnostics must not
        # replace the actual timeout/exit outcome or stringify private objects.
        pass


class CLIWorkspace(tempfile.TemporaryDirectory):
    def __init__(self):
        super().__init__(prefix='wb-generation-cli-')
        self.owned_path=Path(self.name).resolve()
        self.processes_stopped=True  # No process has been launched yet.
        self.cleanup_deadline=None

    def cleanup(self):
        from execution.generation import ContainmentError
        path=Path(self.name)
        if (not self.processes_stopped or path.is_symlink() or
                (hasattr(path,'is_junction') and path.is_junction()) or path.resolve()!=self.owned_path):
            self._finalizer.detach()
            trace_workspace(path,'retained',processes_stopped=self.processes_stopped)
            raise ContainmentError('CLI workspace ownership or process termination unconfirmed')
        attempts=0
        while True:
            attempts+=1
            try:
                super().cleanup()
                trace_workspace(path,'removed',attempts=attempts)
                return
            except PermissionError as error:
                if attempts==1:
                    holders={}
                    if os.environ.get('WB_REGRESSION_DIAGNOSTICS')=='1' and os.environ.get('WB_REGRESSION_EVIDENCE_DIR'):
                        try:
                            holders=directory_holders(path)
                        except (OSError,AttributeError) as query_error:
                            holders={'holder_query_error':type(query_error).__name__}
                    trace_workspace(path,'remove_refused',winerror=getattr(error,'winerror',None),
                                    processes_stopped=True,**holders)
                remaining=0 if self.cleanup_deadline is None else self.cleanup_deadline-time.monotonic()
                if os.name!='nt' or getattr(error,'winerror',None) not in (5,32,33) or remaining<=0:
                    trace_workspace(path,'remove_failed',attempts=attempts,winerror=getattr(error,'winerror',None))
                    raise
                time.sleep(min(.01,remaining))


def close_cli_handles(child):
    """Called after wait/join; no reader or writer may retain a CLI pipe."""
    # The Linux guardian owns stderr (DEVNULL); its child proxy exposes only
    # the captured pipes. Closing an absent stream is not part of the protocol.
    for stream in (child.stdin,child.stdout,getattr(child,'stderr',None)):
        if stream is not None:
            stream.close()
    if os.name=='nt':
        child._handle.Close()
