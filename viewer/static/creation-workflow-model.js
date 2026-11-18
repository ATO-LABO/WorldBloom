/* Shared pure operations for the authoring buffer, not engine inference. */
(function(global){'use strict';
const copy=v=>structuredClone(v),fingerprint=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
function empty(name='',overview=''){return {name,overview,initial_story:'',people:[],places:[],items:[],routes:[],protagonist:'',antagonist:''};}
function initialScreen(params){
 const screen=params.get('screen');
 if(screen==='import')return 'world-import';
 if(['entry','world-import','zip-import','resume','workflow'].includes(screen))return screen;
 if(['workspace','native_draft','draft'].some(k=>/^[a-f0-9]{32}$/.test(params.get(k)||'')))return 'workflow';
 if(params.has('from'))return 'world-import';
 if(['manual','ai'].includes(params.get('method'))||/^[123]$/.test(params.get('step')||''))return 'workflow';
 return 'entry';
}
function rename(d,kind,index,value){
 const rows=d[kind],old=rows[index].name;rows[index].name=value;if(!old||!value||rows.some((r,i)=>i!==index&&r.name===value))return;
 if(kind==='people'){
  for(const key of ['protagonist','antagonist'])if(d[key]===old)d[key]=value;
  for(const p of d.people){for(const r of p.relations||[])if(r.person===old)r.person=value;for(const c of p.goal?.conditions||[])if(c.person===old)c.person=value;}
  for(const item of d.items){if(item.holder===old)item.holder=value;for(const h of item.holders||[])if(h.person===old)h.person=value;}
 }else if(kind==='places'){
  for(const p of d.people){if(p.entry===old)p.entry=value;if(p.goal?.deliver_to===old)p.goal.deliver_to=value;for(const c of p.goal?.conditions||[])if(c.place===old)c.place=value;}
  for(const r of d.routes)for(const k of ['from','to'])if(r[k]===old)r[k]=value;
  for(const f of d.facts||[])if(f.place===old)f.place=value;
 }else if(kind==='items'){
  for(const p of d.people){if(p.goal?.target===old)p.goal.target=value;for(const c of p.goal?.conditions||[])if(c.item===old)c.item=value;}
  for(const r of d.routes)if(r.requires_item===old)r.requires_item=value;
 }
}
function authoring(old,genre,type,outline){return {...(old||{}),version:1,type:type||'',outline:{...(old?.outline||{}),genre:genre||'',introduction:outline===undefined?(old?.outline?.introduction||''):outline||''}};}
const defaultTiming=()=>({days:7,slots:['朝','昼','夕方','夜']});
function validTiming(t){return t&&typeof t==='object'&&Number.isInteger(t.days)&&t.days>=1&&t.days<=10000&&Array.isArray(t.slots)&&t.slots.length>0&&t.slots.every(x=>typeof x==='string'&&x.trim())&&new Set(t.slots).size===t.slots.length;}
function hydrateTiming(d,legacy){
 if(!d||typeof d!=='object'||Array.isArray(d)||d.settings!==undefined&&(d.settings===null||typeof d.settings!=='object'||Array.isArray(d.settings)))return d;
 if(d.settings&&Object.hasOwn(d.settings,'time')||!legacy?.enabled)return d;
 const t={days:legacy.days===''?7:Number(legacy.days),slots:(legacy.slots||'朝\n昼\n夕方\n夜').split(/\r?\n/).map(x=>x.trim())};
 if(validTiming(t)){d.settings??={version:1};d.settings.time=t;}return d;
}
function workspaceTime(d,legacy){
 const t=d?.settings?.time,unspecified=()=>({enabled:false,days:'',slots:'朝\n昼\n夕方\n夜'});
 if(validTiming(t))return {enabled:true,days:String(t.days),slots:t.slots.join('\n')};
 // Legacy migration happens on load, before editing. Absence in the current
 // object is authoritative, including explicit JSON/form/AI removal.
 if(d&&typeof d==='object'&&!Array.isArray(d)&&(d.settings===undefined||d.settings&&typeof d.settings==='object'&&!Array.isArray(d.settings)&&t===undefined))return unspecified();
 // Unparseable drafts or invalid settings retain their legacy payload for repair.
 return copy(legacy||unspecified());
}
function editTime(d,days,slots,initialStory){
 if(!d||typeof d!=='object'||Array.isArray(d)||d.settings!==undefined&&(d.settings===null||typeof d.settings!=='object'||Array.isArray(d.settings)))throw Error('実行設定の形式を下案JSONで確認してください。現在の設定は保持しています。');
 const t=d.settings?.time;if(t!==undefined&&(!t||typeof t!=='object'||Array.isArray(t)))throw Error('時間設定の形式を下案JSONで確認してください。現在の設定は保持しています。');
 const next={days:Number(days),slots:slots.split(/\r?\n/).map(x=>x.trim())};
 if(!String(days).trim()||!validTiming(next))throw Error('期間は1〜10000の整数、時間帯は空行・重複のない名前を1行ずつ入力してください。');
 const result=copy(d);result.settings??={version:1};result.settings.time={...(t||{}),...next};if(initialStory!==undefined)result.initial_story=initialStory;return result;
}
function report(d){const t=d.settings?.time;return {
 missing:[...(!d.name?.trim()?['世界の名前']:[]),...(!d.places?.length?['場所']:[]),...((d.people?.length||0)<2?['人物（採用には2人以上）']:[]),...(!d.protagonist?['主人公']:[]),...(!d.antagonist?['相手役']:[]),...(d.people||[]).flatMap((p,i)=>[...(!p.name?.trim()?['人物'+(i+1)+'の名前']:[]),...(!p.entry?[(p.name||'人物'+(i+1))+'の開始位置']:[]),...(p.name===d.protagonist&&p.goal&&'description'in p.goal&&!p.goal.description.trim()?['主人公の目標（文章）']:[])])],
 defaults:[validTiming(t)?`期間 ${t.days}日間・時間帯 ${t.slots.join('／')}（指定値）`:t?'時間設定の形式を確認してください（元の値を保持）':'期間7日間・朝／昼／夕方／夜（既定値）',...(!d.settings?.action_usage?['行動は共通の初期設定。個別制限は未指定です。']:[]),...(!d.settings?.mechanics?['世界の仕組みは既存の共通初期設定です。']:[]),...(d.people||[]).filter(p=>p.goal&&'description'in p.goal&&!(p.goal.conditions||[]).length).map(p=>(p.name||'名前未設定')+'の目標は文章のみ。自動判定する達成条件は未設定です。'),'数値特性の未指定値は各0.5・基礎値50。人物の意図から数値を推測しません。'],
 counts:{people:d.people?.length||0,places:d.places?.length||0,items:d.items?.length||0}
};}
global.WorldBloomCreationModel={copy,fingerprint,empty,initialScreen,rename,authoring,report,defaultTiming,validTiming,hydrateTiming,workspaceTime,editTime};
})(window);
