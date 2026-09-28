'use strict';
const assert=require('node:assert/strict');
const {WorldRoutes:R}=require('../viewer/static/world-prototype.js');
let checks=0;
function test(name,fn){fn();checks++;console.log('ok '+name);}
const world={protagonist:'桃太郎',time:{days:20,slots:['朝','昼','夕方','夜']},scheduled_events:[]};
const point={day:1,slot:'朝'};
const route={steps:[{kind:'investigate',text:'道中で小判を調べる ×3',zone:'道中',count:3,cost:3,cumulative:3},{kind:'craft',text:'鉄砲を作る',zone:'道中',cost:1,cumulative:4},{kind:'move',text:'森へ移動',zone:'森',cost:1,cumulative:5}]};
test('repeated actions keep individual cost and timestamps',()=>{
 const before=JSON.stringify([route,point,world]),result=R.build(route,point,world);
 assert.equal(result.rows.length,5);assert.equal(result.rows[1].text,'道中で小判を調べる（2/3）');
 assert.deepEqual(result.rows.map(s=>s.cumulative),[1,2,3,4,5]);
 assert.deepEqual(result.rows.map(s=>R.format(s.at)),['1日目 朝','1日目 昼','1日目 夕方','1日目 夜','2日目 朝']);
 assert.equal(R.format(result.rows[3].next),'2日目 朝');assert.equal(JSON.stringify([route,point,world]),before);
});
test('named timepoint preserves its start slot',()=>{
 const result=R.build(route,{day:3,slot:'夕方'},world);assert.equal(R.format(result.rows[0].at),'3日目 夕方');assert.equal(R.format(result.rows[2].at),'4日目 朝');
});
test('day opening starts at the first configured slot',()=>{assert.equal(R.format(R.build(route,{day:3,slot:null},world).rows[0].at),'3日目 朝');});
test('world-specific labels and slot counts',()=>{
 const result=R.build(route,{day:2,slot:'放課後'},{...world,time:{days:5,slots:['昼','放課後','夜']}});
 assert.equal(R.format(result.rows[0].at),'2日目 放課後');assert.equal(result.rows[0].at.slotIndex,2);assert.equal(result.rows[0].at.slotCount,3);assert.equal(R.format(result.rows[2].at),'3日目 昼');
});
const move={day:1,slot:'朝',targets:['桃太郎'],force_action:{verb:'move',args:['道中']}};
test('already-replayed forced movement consumes the baseline slot',()=>{
 const result=R.build(route,point,{...world,scheduled_events:[move]});assert.equal(result.calendar.consumed,true);assert.equal(R.format(result.rows[0].at),'1日目 昼');
});
test('last-slot forced movement advances to next day',()=>{assert.equal(R.format(R.build(route,{day:1,slot:'夜'},{...world,scheduled_events:[{...move,slot:'夜'}]}).rows[0].at),'2日目 朝');});
test('day-opening move is consumed only at the first named slot',()=>{
 const opening={...move,slot:null};
 assert.equal(R.format(R.build(route,point,{...world,scheduled_events:[opening]}).rows[0].at),'1日目 昼');
 assert.equal(R.format(R.build(route,{day:1,slot:'夕方'},{...world,scheduled_events:[opening]}).rows[0].at),'1日目 夕方');
 assert.equal(R.format(R.build(route,{day:1,slot:null},{...world,scheduled_events:[opening]}).rows[0].at),'1日目 朝');
});
test('other characters, past events and non-move actions do not consume the slot',()=>{
 for(const event of [{...move,targets:['犬']},{...move,day:2},{...move,force_action:{verb:'investigate'}}])assert.equal(R.format(R.build(route,point,{...world,scheduled_events:[event]}).rows[0].at),'1日目 朝');
});
test('slot force overrides opening force in replay order',()=>{
 const result=R.build(route,point,{...world,scheduled_events:[move,{...move,slot:null,force_action:{verb:'investigate'}}]});assert.equal(R.format(result.rows[0].at),'1日目 昼');
 const replaced=R.build(route,point,{...world,scheduled_events:[{...move,slot:null},{...move,force_action:{verb:'investigate'}}]});assert.equal(R.format(replaced.rows[0].at),'1日目 朝');
});
test('costs are distinct from action slots and training is marked',()=>{
 const result=R.build({steps:[{kind:'train',text:'鍛錬',cost:10,cumulative:10},{kind:'move',text:'移動',cost:1,cumulative:11}]},point,world);
 assert.equal(result.rows[0].cost,10);assert.equal(result.rows[0].assumed,true);assert.equal(R.format(result.rows[1].at),'1日目 昼');assert.equal(result.rows[1].cumulative,11);
});
test('fight rounds are presented as estimates',()=>{
 const result=R.build({steps:[{kind:'fight',text:'鬼と戦う',cost:3,cumulative:3}]},point,world);
 assert.equal(result.rows.length,3);assert.ok(result.rows.every(s=>s.assumed));assert.equal(result.rows[2].cumulative,3);assert.match(result.rows[0].text,/1\/3・目安/);
});
test('missing or invalid calendar yields no fabricated dates',()=>{
 for(const badWorld of [{...world,time:{slots:[]}},{...world,time:{slots:['朝','朝']}},{...world,time:{slots:[1]}}])assert.equal(R.build(route,point,badWorld).rows[0].at,null);
 assert.equal(R.build(route,{day:1,slot:'未登録'},world).rows[0].at,null);assert.equal(R.format(null),'日時未設定');
});
test('unknown costs stay unknown',()=>{const result=R.build({steps:[{kind:'move',text:'移動'}]},point,world);assert.equal(result.rows[0].cost,null);assert.equal(result.rows[0].cumulative,null);});
test('configured period limit is shown and empty routes stay empty',()=>{
 const result=R.build(route,point,{...world,time:{days:1,slots:['朝','昼','夕方','夜']}});assert.equal(result.rows[3].at.outside,false);assert.equal(result.rows[4].at.outside,true);assert.deepEqual(R.build({steps:[]},point,world).rows,[]);
});
test('very long repeats cannot freeze the workspace',()=>{const result=R.build({steps:[{kind:'investigate',text:'調査',count:1000000,cost:1000000,cumulative:1000000}]},point,world);assert.equal(result.rows.length,5000);assert.equal(result.overflow,true);});
console.log(`${checks} route timeline cases passed`);
