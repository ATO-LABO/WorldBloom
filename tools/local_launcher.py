"""Private, foreground Windows launcher. No install, preload, pull or process kill."""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

SOURCE=Path(__file__).resolve().parents[1]
MODEL='qwen3.8:27b-q4_K_M'
BASE='http://127.0.0.1:11434'
MODEL_DIGEST='25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e'
EXAMPLE='world-9ac84fb8481e46dcbcb430137d1d2294'

def read_local(path):
    with urllib.request.urlopen(BASE+path,timeout=4) as r:return json.load(r)

def compute_processes():
    result=subprocess.run(['nvidia-smi','-q','-x'],
        capture_output=True,text=True,timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:raise RuntimeError('GPU使用状況を読み取れません')
    try:
        root=ET.fromstring(result.stdout)
        if root.tag!='nvidia_smi_log' or not root.findall('gpu'):
            raise RuntimeError('GPUプロセスの一覧を読み取れません')
        return [[p.findtext('pid',''),p.findtext('process_name') or '[Insufficient Permissions]',p.findtext('type','')]
                for p in root.findall('./gpu/processes/process_info')]
    except ET.ParseError:raise RuntimeError('GPUプロセスの一覧を読み取れません') from None

def protected_desktop_context(pid):
    """Read one protected Windows compositor identity, never stop or elevate it."""
    if os.name!='nt' or not pid.isdecimal():return False
    command=(f"$wbGpuProcess=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}';"
        "if($null -eq $wbGpuProcess){exit 1};"
        "$wbGpuOwner=Invoke-CimMethod -InputObject $wbGpuProcess -MethodName GetOwner;"
        "[pscustomobject]@{Name=$wbGpuProcess.Name;Session=$wbGpuProcess.SessionId;"
        "OwnerResult=$wbGpuOwner.ReturnValue;Domain=$wbGpuOwner.Domain;User=$wbGpuOwner.User}|ConvertTo-Json -Compress")
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True,text=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:return False
    try:
        identity=json.loads(result.stdout)
        if not isinstance(identity,dict):return False
        return (identity.get('Name')=='dwm.exe' and type(identity.get('Session')) is int and identity['Session']>0
            and (identity.get('OwnerResult')==2 or (identity.get('Domain')=='Window Manager'
                  and re.fullmatch(r'DWM-\d+',identity.get('User') or ''))))
    except (ValueError,TypeError):return False

def check_gpu(rows):
    # WDDM also lists desktop C+G contexts in --query-compute-apps. Ignore only
    # these known installed UI paths, never arbitrary names such as python.exe.
    # Unknown processes and foreign model runtimes still block. Nothing is killed.
    foreign=[]
    for row in rows:
        if len(row)<2:raise RuntimeError('GPUプロセスの識別結果が不明です')
        pid,path=row[0].strip(),row[1].strip().replace('\\','/').lower()
        if not pid.isdecimal():raise RuntimeError('GPUプロセスの識別結果が不明です')
        windows=os.environ.get('WINDIR',r'C:\Windows').replace('\\','/').lower().rstrip('/')
        program_files=os.environ.get('ProgramFiles',r'C:\Program Files').replace('\\','/').lower().rstrip('/')
        local_appdata=os.environ.get('LOCALAPPDATA','').replace('\\','/').lower().rstrip('/')
        if local_appdata and path==local_appdata+'/programs/ollama/lib/ollama/llama-server.exe':
            continue  # This Ollama version uses this runner; /api/ps is checked below.
        graphics=len(row)>2 and row[2].strip() in ('G','C+G')
        if graphics and (path == windows+'/explorer.exe' or re.fullmatch(re.escape(program_files)+r'/windowsapps/openai\.codex_[^/]+/app/chatgpt\.exe',path)):
            continue
        if graphics and path in ('','[insufficient permissions]') and protected_desktop_context(pid):
            continue
        name=path.split('/')[-1]
        if name not in ('ollama.exe','ollama','ollama_llama_server.exe','ollama_llama_server'):
            foreign.append(pid)
    if foreign:raise RuntimeError('別のGPUプロセスが存在するか識別できません。MOSS/ComfyUI等を本人が停止してから再確認してください。生成は送信していません。')

def check_gpu_headroom():
    result=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
        capture_output=True,text=True,timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:raise RuntimeError('GPUの空きメモリを確認できません')
    try:free=[int(v.strip()) for v in result.stdout.splitlines() if v.strip()]
    except ValueError:raise RuntimeError('GPUの空きメモリを確認できません') from None
    if not free or min(free)<2048:raise RuntimeError('GPUの空きメモリが不足しています。ほかのアプリは停止していません')

def guarded_provider(settings):
    from execution.world_drafts import existing_provider
    from execution.provenance import ConfigError
    # This dedicated 27B profile offloads to CPU on 8GB cards. Keep generic
    # story/output settings intact; bound only this two-stage local draft flow.
    provider=existing_provider(settings,time_budget=(900,1500))
    gate=threading.Lock()
    def invoke(action, *args, **kwargs):
        if not gate.acquire(blocking=False):
            raise ConfigError('generation','この起動版で別の生成が進行中です',code='unavailable')
        try:
            try:
                configured=json.loads(settings.read_text(encoding='utf8')).get('output',{})
                local=configured.get('ollama',{})
                if configured.get('default_backend')!='ollama' or local.get('model')!=MODEL or local.get('base_url')!=BASE or 'api_key' in local:
                    raise RuntimeError('専用Qwen設定が変更されています。外部への送信は行いません。')
                check_gpu(compute_processes())
                installed=[m for m in read_local('/api/tags').get('models',[]) if m.get('name')==MODEL]
                if len(installed)!=1 or installed[0].get('digest')!=MODEL_DIGEST:
                    raise RuntimeError('承認済みのローカルモデルと一致しません。ダウンロードは行いません。')
                loaded=read_local('/api/ps').get('models',[])
                if any(m.get('name')!=MODEL for m in loaded):
                    raise RuntimeError('Ollamaで別のモデルが使用中です。停止や置換は行いません。')
                if not loaded:check_gpu_headroom()
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
                raise ConfigError('generation',str(e) if isinstance(e,RuntimeError) else 'GPUまたはOllamaの確認ができません。生成は送信していません。',code='unavailable') from e
            return action(*args,**kwargs)
        finally:gate.release()
    def generate(name,overview):return invoke(provider,name,overview)
    def review(name,overview,draft,**kwargs):return invoke(provider.review,name,overview,draft,**kwargs)
    generate.review=review
    generate.busy=gate.locked
    return generate

@contextmanager
def instance_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b');locked=False
    try:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        locked=True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()

def prepare(source,state):
    manifest=json.loads((source/'launch-assets/package-files.json').read_text(encoding='utf8'))
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    workspace=state/'workspace'
    if workspace.exists():
        if json.loads((workspace/'.prepared.json').read_text())['package_hash']!=digest:
            raise RuntimeError('別版の作業コピーがあります。既存データを保持しました。新しいフォルダへ解凍して起動してください。')
        return workspace
    stage=state/'workspace.preparing'
    if stage.exists():raise RuntimeError('前のコピー準備が未完了です。workspace.preparingを確認してください。自動削除はしません。')
    stage.mkdir(parents=True)
    for name,expected in manifest.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise RuntimeError('パッケージ内のパスが不正です')
        raw=(source/path).read_bytes()
        # Git checks CMD files out with CRLF on Windows, while its source ZIP
        # contains LF blobs. Accept that one declared checkout transformation;
        # keep every other byte and all other file types under the exact hash.
        matches=hashlib.sha256(raw).hexdigest()==expected
        if not matches and Path(name).suffix.lower()=='.cmd':
            matches=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()==expected
        if not matches:raise RuntimeError('ソースの整合性が一致しません: '+name)
        target=stage/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    # Actual generated example, explicitly named as a test; source stays intact.
    example=stage/'launch-assets/qwen-example'
    if example.exists():shutil.copytree(example,stage/'projects'/EXAMPLE)
    (stage/'settings.json').write_text(json.dumps({'output':{'default_backend':'none'}}),encoding='utf8')
    (stage/'.prepared.json').write_text(json.dumps({'package_hash':digest}),encoding='utf8')
    stage.rename(workspace)
    return workspace

def bind_server(handler,port,strict=False):
    from viewer.server import ViewerServer
    class LocalServer(ViewerServer):
        allow_reuse_address=False
        def server_bind(self):
            if os.name=='nt':self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()
    for candidate in range(port,min(port+(1 if strict else 20),65536)):
        try:return LocalServer(('127.0.0.1',candidate),handler)
        except OSError as e:
            # Windows may report 10013 for a port already held exclusively.
            # Leave that port alone; try another loopback port, never change ACLs.
            if e.errno not in (48,98,10048,10013) and getattr(e,'winerror',None) not in (10048,10013):raise
    raise RuntimeError('候補ポートがすべて使用中です。他のプロセスは停止していません。--portで別の番号を指定してください。')

def build_server(workspace,state,mode,port,strict=False):
    from viewer.server import ViewerHandler
    from viewer.data import RunRepository
    from execution.configs import ConfigStore
    from execution.jobs import JobStore
    from execution.world_drafts import DraftStore
    class Handler(ViewerHandler):
        def do_POST(self):
            if self.path.split('?')[0] in ('/api/status/local/preload','/api/status/local/unload'):
                self._body_consumed=False;self._drain_unread_body()
                self._send_json(403,{'message':'この起動版では他のGPUモデルをロード・解放しません。新規下案の作成を利用してください。'})
            else:super().do_POST()
    server=bind_server(Handler,port,strict)
    (state/'runs').mkdir(exist_ok=True)
    server.job_store=JobStore(ConfigStore(workspace,state/'control',state/'runs'))
    server.settings_path=workspace/'settings.json'
    server.job_store.world_draft_mode='live' if mode=='qwen' else 'waiting'
    server.job_store.world_draft_notice=(
        'ローカルQwen3.8の実生成モードです。生成ボタンを押す時にGPU競合を確認します。MOSS/ComfyUI等が使用中・識別不明なら送信しません。'
        if mode=='qwen' else '画面確認モードです。Qwenを呼ばずGPUを使用しません。生成する場合は起動ウィンドウでq→Enterで停止し、再起動時に2を選んでください。')
    settings=state/'qwen-settings.json'
    if not settings.exists():
        settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{
            'model':MODEL,'base_url':BASE,'think':False,
            'options':{'num_ctx':2048,'num_predict':1600,'temperature':0.2},
            'limits':{'max_calls':1,'call_timeout_seconds':300,'wall_seconds':360,'max_saved_response_bytes':64000}}}}),encoding='utf8')
    # Refuse changed endpoints/models; never load credentials from another checkout.
    config=json.loads(settings.read_text(encoding='utf8'))
    local=config.get('output',{}).get('ollama',{})
    if config.get('output',{}).get('default_backend')!='ollama' or local.get('model')!=MODEL or local.get('base_url')!=BASE or 'api_key' in local:
        server.server_close();raise RuntimeError('専用Qwen設定が変更されています。起動を停止しました。')
    provider=guarded_provider(settings) if mode=='qwen' else None
    server.local_generation_busy=provider.busy if provider else lambda:False
    server.world_drafts=DraftStore(workspace,state/'control',provider,server.job_store.world_draft_mode)
    server.repository=RunRepository(state/'runs',control_root=state/'control',jobs=server.job_store)
    return server

def main(argv=None):
    parser=argparse.ArgumentParser(description='WorldBloom 新規作成確認版の起動・停止')
    parser.add_argument('--mode',choices=('view','qwen'),default='view')
    parser.add_argument('--interactive',action='store_true')
    parser.add_argument('--port',type=int,default=8774)
    parser.add_argument('--strict-port',action='store_true',help='Use only the requested port; never choose another.')
    parser.add_argument('--state-root',type=Path,default=SOURCE/'.local-user')
    args=parser.parse_args(argv)
    if not 1024<=args.port<=65535:parser.error('portは1024〜65535で指定してください')
    mode=args.mode
    if args.interactive:
        print('1: 画面確認（既定・GPUなし）\n2: ローカルQwenで下案を生成（GPU競合時は停止）',flush=True)
        choice=input('選択 [1]: ').strip()
        if choice not in ('','1','2'):raise RuntimeError('1または2を選んでください')
        mode='qwen' if choice=='2' else 'view'
    state=args.state_root.resolve();state.mkdir(parents=True,exist_ok=True)
    try:
        with instance_lock(state/'launcher.lock'):
            workspace=prepare(SOURCE,state)
            sys.path.insert(0,str(workspace))
            import yaml  # existing dependency only; never install automatically
            while True:
                server=build_server(workspace,state,mode,args.port,args.strict_port)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                url=f'http://127.0.0.1:{server.server_address[1]}'
                info={'url':url,'mode':mode,'pid':os.getpid(),'workspace':str(workspace)}
                (state/'status.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf8')
                print('STARTED '+json.dumps(info,ensure_ascii=False),flush=True)
                print('新規作成: '+url+'/worlds/new\n保存済み実生成例: '+url+'/worlds/'+EXAMPLE,flush=True)
                print('停止: q→Enter / Ctrl+C。再起動: r→Enter。作成済みデータは保持します。',flush=True)
                restart=False
                try:
                    while True:
                        try:command=input().strip().lower()
                        except KeyboardInterrupt:command='q'
                        except EOFError:
                            while server.local_generation_busy():time.sleep(.25)
                            command='q'
                        if command in ('q','quit','stop','r') and server.local_generation_busy():
                            print('生成処理中のため停止・再起動を待機しました。応答後にもう一度qまたはrを入力してください。',flush=True)
                            continue
                        if command in ('q','quit','stop'):break
                        if command=='r':restart=True;break
                        print('停止はq、再起動はrを入力してください。URL: '+url,flush=True)
                finally:
                    server.shutdown();server.server_close();thread.join(5)
                    (state/'status.json').write_text(json.dumps({**info,'stopped':True}),encoding='utf8')
                    print('STOPPED '+url,flush=True)
                if not restart:return 0
    except OSError as e:
        if getattr(e,'winerror',None) in (33,36) or getattr(e,'errno',None) in (11,13):
            info=state/'status.json'
            print('既に起動中、またはロックを取得できません。別プロセスは停止しません。',flush=True)
            if info.exists():print(info.read_text(encoding='utf8'),flush=True)
            return 2
        raise

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,ImportError) as e:
        print('起動を停止しました: '+str(e),file=sys.stderr,flush=True)
        raise SystemExit(1)
