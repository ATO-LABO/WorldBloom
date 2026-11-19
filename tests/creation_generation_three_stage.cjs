const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const c={window:{},structuredClone};vm.createContext(c);
for(const n of ['creation-workflow-model','creation-generation'])vm.runInContext(fs.readFileSync(require.resolve('../viewer/static/'+n+'.js'),'utf8'),c);
const G=c.window.WorldBloomCreationGeneration,M=c.window.WorldBloomCreationModel;
const base={version:1,name:'human',overview:'overview',draft_text:JSON.stringify(M.empty('human','overview')),authoring:null,workflow:{version:1,step:1,category:'people',selected:0},time:{enabled:false,days:'',slots:''}};
assert.equal(G.payload(base,'a').strategy,undefined);
assert.equal(G.payload(base,'a','goal-plan-v1').strategy,'goal-plan-v1');
assert.throws(()=>G.payload(base,'a','unsupported'));
const result={request_id:'a'.repeat(32),strategy:'goal-plan-v1',state:'invalid',editable_generated_candidate:true,edit_revision:'unvalidated',draft:{...M.empty(),people:[],places:[]},model_calls:3,goal_plan:{goals:[]},goal_plan_notes:{version:1,origin:'ai_interpretation',notes:[],assumptions:[],supplemented_names:{}}};
assert(G.ready(result));assert(!G.ready({...result,editable_generated_candidate:false}));
const applied=G.entered(base,result);assert.equal(applied.generation.strategy,'goal-plan-v1');assert.equal(applied.generation.model_calls,3);assert.equal(applied.source_revision,'unvalidated');assert.equal(applied.workflow.step,2);assert.equal(result.state,'invalid');
assert(G.phaseText({...result,phase:'generate'}).includes('2/3'));
assert(G.phaseText({...result,phase:'review'}).includes('3/3'));
(async()=>{let calls=[];const disk=new Map(),s=new G.Session({uuid:()=>result.request_id,storage:{setItem:(k,v)=>disk.set(k,v),getItem:k=>disk.get(k)},api:async(path,body)=>{calls.push({path,body});return result;}});
await s.send({id:'b'.repeat(32),revision:'old',content:base},()=>{},'goal-plan-v1');assert.equal(calls[0].body.strategy,'goal-plan-v1');assert.equal(s.receipt.strategy,'goal-plan-v1');assert.equal(s.receipt.state,'ready');await assert.rejects(s.send({content:base},()=>{},'goal-plan-v1'));assert.equal(calls.length,1);console.log('PASS three-stage UI: explicit strategy, legacy retained, editable invalid candidate, interpretation metadata, phase text, durable strategy receipt and no replay.');})().catch(e=>{console.error(e);process.exitCode=1;});

// Progress-only updates must preserve button DOM and keyboard focus.
{const src=fs.readFileSync(require.resolve('../viewer/static/creation-generation.js'),'utf8');
const helper=src.match(/function updateProgress\(result\)\{[^\n]+/)[0];
const sentinel={id:'manual-button'},ctx={mode:'progress',dialog:{open:true},progressLine:{textContent:'old'},phaseText:()=> 'new phase',document:{activeElement:sentinel},rebuilds:0};
ctx.show=()=>{ctx.rebuilds++};ctx.progressActions=()=>{ctx.rebuilds++};vm.createContext(ctx);vm.runInContext(helper+';updateProgress({});',ctx);
assert.equal(ctx.progressLine.textContent,'new phase');assert.equal(ctx.document.activeElement,sentinel);assert.equal(ctx.rebuilds,0);
ctx.mode='recover';vm.runInContext('updateProgress({});',ctx);assert.equal(ctx.rebuilds,2);console.log('PASS progress updates retain action DOM/focus; new presentation builds controls once.');}
