(function(global){'use strict';
const stages={1:'① 下書きを用意',2:'② 内容を整える',3:'③ 確認して採用'};
function normalize(groups){
 const rows=[];
 groups.forEach((group,i)=>{for(const source of group.workspaces||[]){
  const time=typeof source.updated_at==='number'&&Number.isFinite(source.updated_at)?source.updated_at:null;
  rows.push({id:source.workspace_id,kind:i?'native':'new',name:typeof source.name==='string'&&source.name.trim()?source.name:'名前未設定',overview:typeof source.overview==='string'?source.overview:'',step:Number.isInteger(source.workflow_step)&&stages[source.workflow_step]?source.workflow_step:null,updated:time,readOnly:i>0&&typeof source.published_world==='string'&&!!source.published_world,unsupported:!!source.unsupported||!/^[a-f0-9]{32}$/.test(source.workspace_id||'')});
 }});
 return rows.sort((a,b)=>(b.updated||0)-(a.updated||0)||(a.kind+':'+a.id).localeCompare(b.kind+':'+b.id));
}
function stageLabel(step){return stages[step]||'保存段階不明';}
function dateLabel(value){if(value===null||!Number.isFinite(value))return '保存日時不明';const date=new Date(value*1000);return Number.isNaN(date.getTime())?'保存日時不明':'保存：'+date.toLocaleString('ja-JP',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
function render(host,rows,onOpen,doc=host.ownerDocument){
 const node=(tag,text,cls)=>{const e=doc.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 host.replaceChildren();
 for(const row of rows){
  const item=node('li',undefined,'cw-draft-row'),icon=node('span',undefined,'cw-draft-icon'),body=node('div',undefined,'cw-draft-body'),meta=node('div',undefined,'cw-draft-meta');icon.setAttribute('aria-hidden','true');
  const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg'),path=doc.createElementNS('http://www.w3.org/2000/svg','path');
  svg.setAttribute('viewBox','0 0 24 28');path.setAttribute('d','M3 1h12l6 6v19H3z M15 1v7h6 M7 13h10 M7 18h10 M7 23h7');path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.5');path.setAttribute('stroke-linejoin','round');svg.append(path);icon.append(svg);
  body.append(node('h3',row.name),node('p',row.overview||'概要未設定','cw-draft-summary ui-hint'));
  meta.append(node('span',row.unsupported?'未対応の下書き形式':row.readOnly?'作成済み・閲覧専用':stageLabel(row.step),'cw-draft-stage'),node('span',dateLabel(row.updated),'ui-hint'),node('span',row.kind==='native'?'取込下書き':'新規下書き','ui-hint'));
  const open=node('button',row.readOnly?'内容を見る':'再開する','ui-button ui-button--advance');open.type='button';open.dataset.cwResume=row.kind+':'+row.id;open.dataset.cwLocked=String(row.unsupported);open.disabled=row.unsupported;open.setAttribute('aria-label',row.name+(row.readOnly?'を閲覧する':'を再開する'));open.addEventListener('click',()=>onOpen(row.kind,row.id));
  if(row.unsupported)body.append(node('p','この形式には対応していません。元の保存内容は変更しません。','ui-hint'));
  item.append(icon,body,meta,open);host.append(item);
 }
}
const api={normalize,stageLabel,dateLabel,render};if(typeof module==='object'&&module.exports)module.exports=api;global.WorldBloomSavedDrafts=api;
})(typeof window==='object'?window:globalThis);
