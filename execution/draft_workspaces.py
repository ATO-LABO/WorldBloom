"""Versioned, non-executable working copies. Saving never calls a model or publishes.

The raw editor buffer is authoritative, including invalid/incomplete JSON. Native
source snapshots are immutable bytes, never round-tripped through proposal JSON.
"""
from copy import deepcopy
import base64
import json
import math
import re
import time
from pathlib import Path
from uuid import UUID

from execution.provenance import ConfigError, atomic_json, canonical, contained, directory_lock, read_json, sha256

VERSION = 1
MAX_TEXT = 256000
MAX_SOURCE = 8 * 1024 * 1024

def fail(field, text, code='invalid_config'):
    raise ConfigError(field, text, code=code)

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):
        fail('workspace_id','下書きIDを確認してください')
    return UUID(hex=value).hex

def workflow(value):
    if (not isinstance(value,dict) or not {'version','step','category','selected'}<=value.keys() or set(value)-{'version','step','category','selected','consultation'}
        or type(value.get('version')) is not int or value['version']!=1
        or type(value.get('step')) is not int or value['step'] not in (1,2,3)
        or value.get('category') not in ('people','places','items','time','actions')
        or type(value.get('selected')) is not int or not 0<=value['selected']<=1000):
        fail('workflow','作成画面の段階と項目の保存形式を確認してください')
    if 'consultation' in value:
        c=value['consultation']
        if (not isinstance(c,dict) or set(c)!={'instruction','targets'} or not isinstance(c['instruction'],str)
            or len(c['instruction'])>4000 or not isinstance(c['targets'],dict)
            or any(k not in ('people','places','items','time','actions') or not isinstance(v,str) or len(v)>1000 for k,v in c['targets'].items())):
            fail('workflow.consultation','AIへの要望と選択項目を確認してください')
    return deepcopy(value)

def generation_metadata(value):
    """Non-executable AI interpretation notes, never proof of human approval."""
    keys = {'strategy', 'goal_plan', 'goal_plan_notes', 'model_calls'}
    if (not isinstance(value, dict) or set(value) - keys or
            value.get('strategy') != 'goal-plan-v1' or
            type(value.get('model_calls')) is not int or not 0 <= value['model_calls'] <= 3):
        fail('generation', 'Invalid three-stage interpretation metadata')
    if 'goal_plan' in value and not isinstance(value['goal_plan'], dict):
        fail('generation.goal_plan', 'Interpretation plan must be an object')
    if 'goal_plan_notes' in value:
        notes = value['goal_plan_notes']
        if (not isinstance(notes, dict) or notes.get('version') != 1 or
                notes.get('origin') != 'ai_interpretation' or
                not isinstance(notes.get('notes'), list) or
                not isinstance(notes.get('assumptions'), list) or
                not isinstance(notes.get('supplemented_names'), dict)):
            fail('generation.goal_plan_notes', 'AI interpretation notes must be labelled')
    try:
        encoded = canonical(value)
    except (ValueError, TypeError, RecursionError):
        fail('generation', 'Invalid interpretation JSON')
    if len(encoded) > 64000:
        fail('generation', 'Interpretation metadata must be within 64KB')
    return deepcopy(value)


def payload(value):
    keys={'version','name','overview','time','draft_text','source_draft_id'}
    if not isinstance(value,dict) or not keys<=value.keys() or set(value)-keys-{'source_revision','authoring','workflow','generation'} or value.get('version')!=VERSION or type(value.get('version')) is not int:
        fail('workspace.version','対応していない下書き形式です。元の入力は変更しません','unsupported')
    for key,limit in [('name',120),('overview',8000),('draft_text',MAX_TEXT)]:
        if not isinstance(value[key],str) or len(value[key].encode('utf-8'))>limit*4:
            fail(key,'下書きの文字数を確認してください')
    timing=value['time']
    if not isinstance(timing,dict) or set(timing)!={'enabled','days','slots'} or type(timing['enabled']) is not bool or any(not isinstance(timing[k],str) or len(timing[k])>16000 for k in ['days','slots']):
        fail('time','時間欄の保存形式を確認してください')
    if value['source_draft_id'] is not None:identifier(value['source_draft_id'])
    if 'authoring' in value:
        from execution.authoring_context import normalize
        normalize(value['authoring'])
    if value.get('source_revision') is not None and (not isinstance(value['source_revision'],str) or len(value['source_revision'])>128):
        fail('source_revision','元の下案の版を確認してください')
    if 'workflow' in value:workflow(value['workflow'])
    if 'generation' in value:generation_metadata(value['generation'])
    if len(canonical(value))>MAX_TEXT:fail('workspace','下書きは256KB以内にしてください')
    return deepcopy(value)

class WorkspaceStore:
    def __init__(self,repo,control):
        self.repo=Path(repo);self.root=contained(control,'world-draft-workspaces')

    def _path(self,token):return contained(self.root,identifier(token)+'.json')

    def get(self,token):
        try:r=read_json(self._path(token))
        except FileNotFoundError:fail('workspace_id','下書きが見つかりません','not_found')
        if r.get('schema_version')!=VERSION:fail('workspace.version','この下書きは新しい形式です。上書きせず保管します','unsupported')
        payload(r['content'])
        return r

    def list(self, *, native=False):
        rows=[]
        for path in sorted(self.root.glob('*.json')):
            try:
                r=self.get(path.stem)
                content=r['content'];flow=content.get('workflow')
                if native:
                    try:
                        buffer=json.loads(content['draft_text'])
                        flow=buffer.get('workflow') if isinstance(buffer,dict) else None
                    except (ValueError,TypeError):flow=None
                step=flow.get('step') if isinstance(flow,dict) and type(flow.get('version')) is int and flow.get('version')==1 else None
                if type(step) is not int or step not in (1,2,3):step=None
                updated=r.get('updated_at')
                if type(updated) not in (int,float) or not math.isfinite(updated):updated=None
                rows.append({'workspace_id':r['workspace_id'],'name':content['name'],
                    'overview':content['overview'][:240],'workflow_step':step,
                    'updated_at':updated,'revision':r['revision'],
                    'source_world_id':(r.get('source_snapshot') or {}).get('world_id')})
                if native and r.get('published_world'):rows[-1]['published_world']=r['published_world']
            except (ConfigError,ValueError,KeyError,TypeError):
                rows.append({'workspace_id':path.stem,'name':'形式を確認できない下書き（上書きしません）','unsupported':True})
        return sorted(rows,key=lambda r:r.get('updated_at') or 0,reverse=True)

    def _snapshot(self,world_id):
        from execution.library import LibraryStore, identifier as world_identifier
        world_id=world_identifier(world_id,'world_id')
        root=contained(self.repo,'projects/'+world_id)
        if not (root/'world.yaml').is_file():fail('source_world_id','元の世界がありません','not_found')
        LibraryStore._validate_saved_world(root)
        # Preserve every project file, not just the narrow proposal's supported keys.
        files={};total=0
        paths=set(root.rglob('*'))
        # Keep the referenced template directories, including related YAMLs,
        # instead of reducing an existing world to proposal-supported fields.
        import yaml
        initial_world=(root/'world.yaml').read_bytes()
        world=yaml.safe_load(initial_world)
        def references(value):
            if isinstance(value,dict):
                for v in value.values():yield from references(v)
            elif isinstance(value,list):
                for v in value:yield from references(v)
            elif isinstance(value,str) and value.startswith('templates/'):
                parts=Path(value).parts
                if len(parts)>=3:
                    template_id=world_identifier(parts[1],'template_id')
                    template=contained(self.repo,'templates/'+template_id)
                    if not template.is_dir():fail('source_world_id','参照する基本ルールがありません')
                    yield template
        for template in set(references(world)):
            paths.update(template.rglob('*'))
        for p in sorted(paths):
            if p.is_symlink():fail('source_world_id','シンボリックリンクを含む原本は保全できません')
            if not p.is_file() or p.name=='.write.lock':continue
            relative=p.relative_to(self.repo).as_posix();contained(self.repo,relative)
            b=p.read_bytes();total+=len(b)
            if total>MAX_SOURCE:fail('source_world_id','原本の保全は8MB以内です。元の世界は変更しません')
            files[relative]={'base64':base64.b64encode(b).decode('ascii'),'sha256':sha256(b),'bytes':len(b)}
        for relative,entry in files.items():
            if sha256(contained(self.repo,relative).read_bytes())!=entry['sha256']:
                fail('source_world_id','保全中に元の設定が変わりました。上書きせず停止します','conflict')
        if (root/'world.yaml').read_bytes()!=initial_world:
            fail('source_world_id','保全中に世界の参照が変わりました。停止します','conflict')
        return {'version':1,'world_id':world_id,'files':files,'revision':sha256(canonical(files)),
                'scope':'project and referenced template directories; immutable bytes; no publication'}

    def save(self,token,revision,request_id,content,*,source_world_id=None,source_workspace_id=None):
        path=self._path(token);identifier(request_id);content=payload(content)
        if source_workspace_id is not None:identifier(source_workspace_id)
        if source_workspace_id and source_world_id:fail('source','原本の指定は一つにしてください')
        digest=sha256(canonical({'revision':revision,'content':content,'source_world_id':source_world_id,'source_workspace_id':source_workspace_id}))
        with directory_lock(self.root):
            old=self.get(token) if path.exists() else None
            if old and old.get('published_world'):fail('workspace','作成済みの取込下書きは閲覧専用です','conflict')
            receipt_path=contained(self.root,f'receipts/{identifier(token)}/{identifier(request_id)}.json')
            if old and old.get('last_request_id')==request_id:
                if old.get('save_digest')!=digest:fail('request_id','同じ保存IDで内容を変更できません','conflict')
                return old
            if receipt_path.exists():
                receipt=read_json(receipt_path)
                if receipt['digest']!=digest:fail('request_id','同じ保存IDで内容を変更できません','conflict')
                # An uncertain response is reconciled without rolling a newer edit back.
                if not old or old['revision']!=receipt['revision']:fail('revision','保存後に別の編集があります。最新状態を確認してください','conflict')
                return old
            if old:
                if revision!=old['revision']:fail('revision','別の画面で下書きが更新されています。入力を残して保存を止めました','conflict')
                if source_world_id is not None or source_workspace_id is not None:fail('source_world_id','保全した原本は差し替えできません')
            elif revision is not None:fail('revision','新しい下書きの版が一致しません','conflict')
            source=deepcopy(old.get('source_snapshot')) if old else self._snapshot(source_world_id) if source_world_id else None
            if not old and source_workspace_id:source=deepcopy(self.get(source_workspace_id).get('source_snapshot'))
            sequence=old['sequence']+1 if old else 1
            r={'schema_version':VERSION,'workspace_id':token,'sequence':sequence,'content':content,
               'source_snapshot':source,'updated_at':time.time(),'last_request_id':request_id}
            r['revision']=sha256(canonical(r))
            if old:atomic_json(contained(self.root,f'history/{token}/{old["sequence"]}.json'),old)
            # Record contains its receipt too, so a receipt-file failure cannot cause
            # duplicate writes. The next save recognizes the last request below.
            r['save_digest']=digest
            atomic_json(path,r)
            atomic_json(receipt_path,{'digest':digest,'revision':r['revision']})
            return r
