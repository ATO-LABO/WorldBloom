const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const context={window:{},structuredClone};vm.runInNewContext(fs.readFileSync(require.resolve('../viewer/static/creation-workflow-model.js'),'utf8'),context);const M=context.window.WorldBloomCreationModel;
let d=M.empty('','途中');assert.equal(d.people.length,0);assert.equal(d.places.length,0);assert(M.report(d).missing.includes('世界の名前'));
d.people=[{name:'甲',entry:'港',relations:[{person:'乙'}],goal:{conditions:[{type:'meet',person:'乙'}]}},{name:'乙',entry:'港',relations:[],goal:{conditions:[]}}];d.places=[{name:'港',description:''}];d.items=[{name:'鍵',holders:[{person:'乙',count:2}]}];d.protagonist='甲';d.antagonist='乙';
M.rename(d,'people',1,'丙');assert.equal(d.antagonist,'丙');assert.equal(d.people[0].relations[0].person,'丙');assert.equal(d.items[0].holders[0].person,'丙');assert.equal(d.people[0].goal.conditions[0].person,'丙');
M.rename(d,'places',0,'町');assert.equal(d.people[0].entry,'町');
const a=M.authoring({version:1,type:'future',outline:{unknown:'保持'}},'任意ジャンル','','');assert.equal(a.outline.unknown,'保持');assert.equal(a.outline.genre,'任意ジャンル');assert.equal(d.settings,undefined);
assert.equal(M.fingerprint({b:1,a:{z:2,y:3}}),M.fingerprint({a:{y:3,z:2},b:1}));console.log('PASS creation model: empty manual start, explicit refs, metadata only, incomplete summary');
for(const [query,expected]of [
 ['', 'entry'],['mode=create','entry'],['screen=import','world-import'],['screen=world-import','world-import'],['screen=zip-import','zip-import'],['screen=resume','resume'],['screen=workflow','workflow'],
 ['method=manual','workflow'],['method=ai','workflow'],['step=2','workflow'],['from=original','world-import'],
 ['workspace='+'a'.repeat(32),'workflow'],['native_draft='+'b'.repeat(32),'workflow'],['draft='+'c'.repeat(32),'workflow'],
 ['screen=entry&workspace='+'a'.repeat(32),'entry'],['screen=resume&native_draft='+'b'.repeat(32),'resume'],
 ['screen=bad','entry'],['workspace=../../secret','entry'],['method=manual&from=original','world-import']
])assert.equal(M.initialScreen(new URLSearchParams(query)),expected,query);
console.log('PASS entry routing: four choices, saved/deep links, explicit return view and legacy methods');
