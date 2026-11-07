/* Explicit same-draft choices. Reads and cancellation never change the held input. */
(function(global){'use strict';
const copy=v=>structuredClone(v),fingerprint=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const content=(kind,r)=>kind==='native'?r.buffer:r.content;
const identity=(kind,r)=>fingerprint({revision:r.revision,published_world:r.published_world||null,content:content(kind,r)});
class Session{
 constructor({kind,id,local,api,uuid}){this.kind=kind;this.id=id;this.local=copy(local);this.api=api;this.uuid=uuid;this.latest=null;this.busy=false;this.pending=null;this.uncertain=false;}
 get path(){return (this.kind==='native'?'/api/native-world-drafts/':'/api/world-draft-workspaces/')+this.id;}
 async read(){if(this.busy)throw Error('処理中です');this.busy=true;try{this.latest=copy(await this.api(this.path));return copy(this.latest);}finally{this.busy=false;}}
 get readOnly(){return this.kind==='native'&&!!this.latest?.published_world;}
 value(side){if(side==='current')return copy(this.local);if(side==='saved'&&this.latest)return copy(content(this.kind,this.latest));throw Error('比較する内容を選択してください');}
 async commit(side,prepare){
  if(this.busy)throw Error('処理中です');if(this.uncertain)throw Error('送信結果を読み取り確認してください');if(!this.latest)throw Error('保存済みの内容を読み込んでください');
  if(this.readOnly&&side!=='saved')throw Object.assign(Error('作成済みの取込下書きは閲覧専用です'),{known:true});
  this.busy=true;
  try{
   if(this.readOnly){const latest=await this.api(this.path);if(identity(this.kind,latest)!==identity(this.kind,this.latest))throw Object.assign(Error('保存内容が再更新されました。比較を更新してください'),{known:true,status:409});return latest;}
   const chosen=this.value(side),request_id=this.uuid();
   this.pending={action:'resolve',kind:this.kind,id:this.id,content:copy(chosen),held:copy(this.local),request_id};prepare(copy(this.pending));
   return await this.api(this.path+(this.kind==='native'?'/save':''),{revision:this.latest.revision,request_id,[this.kind==='native'?'buffer':'content']:chosen});
  }catch(error){if(!error.known)this.uncertain=true;throw error;}finally{this.busy=false;}
 }
}
function differences(a,b){
 const rows=[];
 function visit(left,right,path){
  if(fingerprint(left)===fingerprint(right))return;
  if(left&&right&&typeof left==='object'&&typeof right==='object'&&!Array.isArray(left)&&!Array.isArray(right)){for(const key of new Set([...Object.keys(left),...Object.keys(right)]))visit(left[key],right[key],path?path+'.'+key:key);return;}
  rows.push({path:path||'内容',current:left,saved:right});
 }
 visit(a,b,'');return rows;
}
function display(kind,value,model,effective){
 const result=copy(value);
 if(kind==='native'&&model&&typeof effective==='function'){try{return {world:effective(model,result.edits),workflow:result.workflow||null};}catch(_){return result;}}
 if(typeof result.draft_text==='string')try{result.draft_text=JSON.parse(result.draft_text);if(result.draft_text&&typeof result.draft_text==='object')for(const key of ['name','overview'])if(result.draft_text[key]===result[key])delete result.draft_text[key];}catch(_){}
 return result;
}
const api={Session,content,identity,differences,fingerprint,display};if(typeof module==='object'&&module.exports)module.exports=api;global.WorldBloomCreationConflict=api;
})(typeof window==='object'?window:globalThis);
