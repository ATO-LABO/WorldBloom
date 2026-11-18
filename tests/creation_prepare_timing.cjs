const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{},structuredClone};vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../viewer/static/creation-workflow-model.js'),'utf8'),context);const M=context.window.WorldBloomCreationModel,plain=x=>JSON.parse(JSON.stringify(x));
const legacy={enabled:true,days:'9',slots:'早朝\n夜'};
let d=M.empty();M.hydrateTiming(d,legacy);assert.deepEqual(plain(d.settings.time),{days:9,slots:['早朝','夜']});
d=M.empty();d.settings={version:1,time:{days:12,slots:['明','暗'],presentation:{calendar:'ordinal',advance:'bands'},date_calendar:{keep:'calendar'},future:'keep'}};
const old=structuredClone(d);M.hydrateTiming(d,legacy);assert.deepEqual(plain(d),plain(old));assert.deepEqual(plain(M.workspaceTime(d,legacy)),{enabled:true,days:'12',slots:'明\n暗'});
assert.deepEqual(plain(M.workspaceTime(d,{enabled:false,days:'',slots:''})),{enabled:true,days:'12',slots:'明\n暗'});
const next=M.editTime(d,'15','昼\n夕方','実際の導入');assert.equal(next.settings.time.days,15);assert.equal(next.settings.time.future,'keep');assert.deepEqual(plain(next.settings.time.date_calendar),{keep:'calendar'});assert.deepEqual(plain(next.settings.time.presentation),plain(d.settings.time.presentation));assert.equal(next.initial_story,'実際の導入');assert.deepEqual(plain(d),plain(old));
for(const [days,slots] of [['','朝'],['0','朝'],['1.5','朝'],['10001','朝'],['7','朝\n朝'],['7','朝\n'],['7','']])assert.throws(()=>M.editTime(d,days,slots));
for(const invalid of [null,'invalid',{}, {days:0,slots:[]}, {days:7,slots:'invalid'}]){const value=M.empty();value.settings={version:1,time:invalid};const before=plain(value);M.hydrateTiming(value,legacy);assert.deepEqual(plain(value),before);}
const broken=M.empty();broken.settings=null;assert.throws(()=>M.editTime(broken,'7','朝'));assert.equal(broken.settings,null);
const meta={version:1,type:'future-type',outline:{genre:'旧ジャンル',introduction:'旧導入メモ',future:'未対応項目'}};
const a=M.authoring(meta,'変更ジャンル',meta.type);assert.equal(a.outline.introduction,'旧導入メモ');assert.equal(a.outline.future,'未対応項目');assert.equal(a.type,'future-type');assert.equal(meta.outline.genre,'旧ジャンル');
// Exercise the controller's actual content function: latest draft settings drive every save,
// including JSON/form/AI replacement, without any stale prepare-time copy.
const src=fs.readFileSync(require.resolve('../viewer/static/creation-workflow.js'),'utf8');
const c={M,copy:structuredClone,S:{kind:'new',draft:next,originalContent:{time:legacy},prep:{name:'new name',overview:'edited overview'},raw:JSON.stringify(next)},workflow:()=>({version:1,step:1}),authoring:()=>a};
vm.createContext(c);vm.runInContext(src.split('\n').find(l=>l.startsWith('function content()')),c);
let value=c.content();assert.equal(value.time.days,'15');assert.equal(value.time.slots,'昼\n夕方');assert.equal(c.S.draft.settings.time.days,15);
for(const route of ['JSON','AI apply','AI recovery','detail apply','GET resume']){c.S.draft=M.editTime(c.S.draft,'18','午前\n午後');c.S.raw=JSON.stringify(c.S.draft);value=c.content();assert.equal(value.time.days,'18',route);assert.equal(value.time.slots,'午前\n午後',route);}
assert(!src.includes('prep.days')&&!src.includes('prep.slots')&&!src.includes('syncTiming')&&!src.includes("$('preserve')"));
// The first legacy load hydrates once. A later explicit omission in a parsed
// draft must serialize a disabled compatibility projection, not resurrect it.
for(const removal of ['time','settings','undefined-time']){
 const first=M.empty();M.hydrateTiming(first,legacy);assert.equal(first.settings.time.days,9);
 c.S.draft=structuredClone(first);
 if(removal==='settings')delete c.S.draft.settings;
 else if(removal==='undefined-time')c.S.draft.settings.time=undefined;
 else delete c.S.draft.settings.time;
 c.S.raw=JSON.stringify(c.S.draft);c.S.originalContent={time:legacy};const saved=c.content();
 assert.equal(saved.time.enabled,false,removal);assert.equal(saved.time.days,'',removal);
 const reopened=JSON.parse(saved.draft_text);M.hydrateTiming(reopened,saved.time);assert.equal(reopened.settings?.time,undefined,removal);
 assert.equal(M.workspaceTime(reopened,saved.time).enabled,false,removal);
}
c.S.draft=null;c.S.raw='{ invalid JSON';c.S.originalContent={time:legacy};assert.deepEqual(plain(c.content().time),legacy);assert.equal(c.content().draft_text,'{ invalid JSON');
for(const settings of [null,'invalid-settings',{time:null},{time:{days:0,slots:[]}}]){
 const value=M.empty();value.settings=settings;const before=plain(value);M.hydrateTiming(value,legacy);assert.deepEqual(plain(value),before);assert.deepEqual(plain(M.workspaceTime(value,legacy)),legacy);
}
console.log('PASS prepare/time: legacy migration, draft precedence, disabled legacy, immutable edits, unknown calendar metadata, invalid preservation, old authoring/type, actual save projection after form/JSON/AI replacement.');
console.log('PASS explicit time/settings removal: disabled compatibility projection survives save/reopen; initial legacy migration and invalid JSON/settings remain protected.');
