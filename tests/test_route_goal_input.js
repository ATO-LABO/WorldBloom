const assert = require('node:assert/strict');
const model = require('../viewer/static/route-goal.js');
const rows = [
  {predicate:'at',subject:'主役',value:'体育館'},
  {predicate:'holds',subject:'主役',value:'衣装',quantity:'2'},
  {predicate:'knows',subject:'実行委員',value:'合図'}
];
const any = model.input('generic','主役',rows,'any','15:00');
assert.deepEqual(any.time,{mode:'any'});
assert.equal(any.all[0].zone,'体育館'); assert.equal(any.all[1].quantity,2); assert.equal(any.all[2].fact,'合図');
assert.equal('quantity' in any.all[0],false); assert.equal('value' in any.all[0],false); assert.equal('legacy' in any,false);
for(const mode of ['at','by']) assert.deepEqual(model.input('generic','主役',rows,mode,'15:00').time,{mode,day:1,slot:'15:00'});
assert.equal(model.input('legacy','主役',rows,'any',''),null);
assert.throws(() => model.input('generic','主役',[{predicate:'OR'}],'any',''));
assert.equal(model.input('generic','主役',[],'any','').all.length,0); // server must reject, never silently synthesize
assert.equal(new Set(['found','unreachable','cutoff','unsupported','invalid_config'].map(model.label)).size,5);
assert.match(model.label('cutoff'),/未判定/); assert.match(model.label('unreachable'),/世界全体の証明ではありません/);
console.log('route goal input and result distinctions: OK');
