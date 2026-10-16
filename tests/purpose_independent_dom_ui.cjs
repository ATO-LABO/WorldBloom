const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {environment}=require('./purpose_dom_ui.cjs');
const receipt=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_independent_real_result.json'),'utf8'));
const tick=()=>new Promise(resolve=>setImmediate(resolve)),KEY='worldbloom-independent-job-v1';
(async()=>{
  const e=environment(),C=e.fields.status.constructor,fields={};
  for(const n of ['config','seeds','start','retry','cancel','status','plan'])fields[n]=new C();
  Object.defineProperty(fields.config,'options',{get(){return this.children;}});fields.seeds.value='0';
  const root=new C();root.querySelector=s=>fields[s.match(/data-independent-(.*)\]/)[1]];
  e.context.document.querySelector=s=>s==='[data-independent-root]'?root:s==='[data-purpose-world]'?e.fields.world:null;
  e.context.crypto=crypto.webcrypto;e.context.TextEncoder=TextEncoder;e.context.Uint8Array=Uint8Array;
  let timers=new Map(),serial=0,postCount=0,capResolve,postResolve,cancelCount=0;
  e.context.setTimeout=fn=>{timers.set(++serial,fn);return serial;};e.context.clearTimeout=id=>timers.delete(id);
  const H=receipt.world_revision,cap={schema_version:1,supported:true,config_id:'cfg-test',world:receipt.world,method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,max_trials:100,...Object.fromEntries(['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'].map(k=>[k,H]))};
  const response=v=>({ok:true,status:200,json:async()=>v});let job;
  e.context.fetch=async(url,options)=>{
    if(url==='/api/independent/capabilities')return response({configurations:[cap]});
    if(url.includes('capabilities?'))return new Promise(resolve=>{capResolve=()=>resolve(response(cap));});
    if(url==='/api/independent/jobs'){postCount++;const request=JSON.parse(options.body);assert.equal(options.headers['X-WorldBloom-Client'],'1');job={job_id:receipt.id,request_id:request.request_id,accepted_request:request,execution_plan_sha256:receipt.execution_plan_sha256,state:'running',phase:'evaluating_independent',revision:1,termination_confirmed:false,progress:{completed:0,requested:1}};return new Promise(resolve=>{postResolve=()=>resolve(response(job));});}
    if(url.endsWith('/cancel')){cancelCount++;job={...job,state:'stopping',cancel_requested_at:1,revision:2};return response(job);}
    if(url.endsWith('/result'))return response(receipt);
    return response(job);
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../viewer/static/purpose-independent.js'),'utf8'),e.context);await tick();fields.config.value='cfg-test';
  fields.start.fire('click');capResolve();await tick();fields.start.fire('click');assert.equal(postCount,1);assert.equal(fields.start.disabled,true);
  e.select.value='create';e.select.fire('change');postResolve();await tick();assert.equal(e.select.value,'create');assert.equal(postCount,1);
  fields.cancel.fire('click');await tick();assert.equal(cancelCount,1);assert.match(fields.status.textContent,/所有プロセスの終了確認/);assert.equal(JSON.parse(e.map.get(KEY)).active.job.termination_confirmed,false);
  e.fire('pagehide');assert.equal(cancelCount,1);assert.equal(JSON.parse(e.map.get(KEY)).active.job.job_id,receipt.id);
  job={...job,state:'cancelled',termination_confirmed:true,revision:3};e.fire('pageshow',{persisted:true});await tick();await tick();await tick();
  // This mock serves a completed saved receipt after terminal confirmation.
  assert.equal(JSON.parse(e.map.get(KEY)).active.job.state,'cancelled');assert.equal(postCount,1);assert.equal(cancelCount,1);
  for(let i=0;i<30&&!e.stored().history.length;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(e.stored().history[0]?.schema,'worldbloom_independent_v2',fields.status.textContent);assert.match(e.fields.result.textContent,/母数 1件/);
  e.select.value='review';e.select.fire('change');assert.equal(JSON.parse(e.map.get(KEY)).active.job.job_id,receipt.id);
  console.log('Connected independent DOM checks passed: start burst, mode switch, CSRF header, cancellation versus exit confirmation, pagehide without cancellation, BFCache same job, saved receipt into immutable history. Mock transport; no HTTP sockets.');
})().catch(e=>{console.error(e);process.exitCode=1;});
