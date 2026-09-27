/* Run with node tests/test_relation_graph.js. No dependencies or DOM mocks. */
'use strict';
const assert=require('node:assert/strict');
const R=require('../viewer/static/world-prototype.js');
const people=[
  {id:'桃太郎',range:{entry:'村'},relations:{犬:{affinity:.9,awareness:.8},鬼:{affinity:-.9}}},
  {id:'犬',affiliation:'旅',relations:{桃太郎:{affinity:.2,awareness:0},猿:{affinity:0,label:'仲間'}}},
  {id:'猿',affiliation:'旅',relations:{鬼:{affinity:-.2,label:'敵対'}}},
  {id:'鬼',range:{entry:'鬼ヶ島'},relations:{}},
  {id:'孤立',relations:{}},
  {id:'入方向',relations:{桃太郎:{affinity:.1,source:'偽',target:'偽'}}}
];
const before=JSON.stringify(people), graph=R.buildRelationGraph(people);
assert.equal(graph.nodes.length,6);assert.equal(graph.edges.length,6);
assert.equal(JSON.stringify(people),before,'model must not mutate saved data');
assert.equal(R.affiliation({affiliation:'  ',range:{entry:'村'}}),'村');
assert.equal(R.affiliation({affiliation:'旅',range:{entry:'村'}}),'旅');
assert.equal(R.affiliation({}),'その他');
assert.ok(graph.edges.some(e=>e.source==='入方向'&&e.target==='桃太郎'),'relation fields cannot override identity');
const pairs=R.buildVisualEdges(graph), pair=pairs.find(e=>e.a==='桃太郎'&&e.b==='犬');
assert.equal(pairs.length,5);assert.equal(pair.directions.length,2);
assert.deepEqual(pair.directions.map(e=>e.affinity).sort(),[.2,.9]);
const direct=R.filterRelationGraph(graph,{mode:'focus',focusId:'桃太郎',scope:'direct'});
assert.deepEqual(direct.nodes.map(n=>n.id),['桃太郎','犬','鬼','入方向']);
const two=R.filterRelationGraph(graph,{mode:'focus',focusId:'桃太郎',scope:'two'});
assert.equal(two.nodes.length,5);assert.ok(two.edges.some(e=>e.source==='犬'&&e.target==='猿'));
assert.equal(R.filterRelationGraph(graph,{mode:'focus',focusId:'桃太郎',scope:'all'}).nodes.length,6);
assert.deepEqual(R.filterRelationGraph(graph,{mode:'focus',focusId:'桃太郎',group:'旅'}).nodes.map(n=>n.id),['桃太郎','犬','猿']);
assert.equal(R.filterRelationGraph(graph,{kind:'neutral'}).edges.length,1,'zero affinity is a real relation');
assert.equal(R.filterRelationGraph(graph,{kind:'negative'}).edges.length,2);
assert.deepEqual(R.layoutRelationGraph(graph),R.layoutRelationGraph({...graph,nodes:[...graph.nodes].reverse()}),'layout must not depend on input order');
const malformed=R.buildRelationGraph([{id:'a',relations:{missing:{affinity:0},bad:null}},{id:'bad',relations:[]},{id:'duplicate'},{id:'duplicate'}]);
assert.equal(malformed.edges.length,0);assert.equal(malformed.issues.length,4);
for(const count of [0,1,30,60]) {
  const g=R.buildRelationGraph(Array.from({length:count},(_,i)=>({id:`人物${i}`,affiliation:`所属${i%3}`})));
  const l=R.layoutRelationGraph(g);assert.equal(l.nodes.length,count);
  assert.ok(l.nodes.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)));
  for(let a=0;a<l.nodes.length;a++) for(let b=a+1;b<l.nodes.length;b++) assert.ok(Math.hypot(l.nodes[a].x-l.nodes[b].x,l.nodes[a].y-l.nodes[b].y)>86,'nodes must not overlap');
}
console.log('Directed graph, focus, filters, deterministic layout, malformed data and 0/1/30/60 people: passed');
