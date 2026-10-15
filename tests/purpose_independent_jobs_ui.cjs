const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {seeds,makeRequest,Session}=require('../viewer/static/purpose-independent.js');
const purpose=require('../viewer/static/purpose-workspace.js');
const H='1'.repeat(64),cap={schema_version:1,supported:true,config_id:'cfg-fixture',world:'momotaro',method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,max_trials:100,...Object.fromEntries(['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'].map(k=>[k,H]))};
const real=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_independent_real_result.json'),'utf8'));
const digest=async v=>crypto.createHash('sha256').update(v,'utf8').digest('hex');
(async()=>{
  assert.deepEqual(seeds('0, 3\n9'),[0,3,9]);for(const value of ['', '1,1','-1','2.5','2147483648'])assert.throws(()=>seeds(value));
  assert.throws(()=>makeRequest({...cap,supported:false},'0','review-test'));
  let saved,sent=0,resolve;const s=new Session({},v=>{saved=v;});const request=makeRequest(cap,'0,1','review-test');
  const job={job_id:'job-a',request_id:request.request_id,accepted_request:request,execution_plan_sha256:H,state:'running',phase:'evaluating_independent',revision:1,termination_confirmed:false};
  const pending=s.start(request,r=>{sent++;assert.deepEqual(r,request);return new Promise(a=>{resolve=a;});});
  assert.equal(await s.start(request,()=>{sent++;}),null);assert.equal(sent,1);assert.equal(saved.active.request.request_id,'review-test');
  s.suspend();resolve(job);assert.equal(await pending,null);assert.equal(s.active.job,null);
  const revisited=new Session(saved,v=>{saved=v;});await revisited.retry(async r=>{sent++;assert.deepEqual(r,request);return job;});assert.equal(sent,2);assert.equal(revisited.active.job.job_id,'job-a');
  assert.throws(()=>revisited.acceptJob({...job,job_id:'other'}));
  await revisited.cancel(async id=>{assert.equal(id,'job-a');return {...job,state:'stopping',revision:2,cancel_requested_at:1};});
  assert.equal(revisited.active.job.state,'stopping');assert.equal(revisited.active.job.termination_confirmed,false);
  assert.equal(revisited.acceptJob(job).state,'stopping'); // stale poll cannot revert cancellation
  revisited.acceptJob({...job,state:'cancelled',revision:3,termination_confirmed:true});assert.equal(revisited.active.job.state,'cancelled');
  assert.equal(purpose.validateReceipt(real).trials.length,1);await purpose.verifyRunRecord(real,digest);
  const corrupt=structuredClone(real);corrupt.trials[0].run_record.verified_execution.seed=100;assert.throws(()=>purpose.validateReceipt(corrupt));
  const mismatched=structuredClone(real);mismatched.trials[0].run_record.execution_plan.provider.id='other';await assert.rejects(()=>purpose.verifyRunRecord(mismatched,digest));
  const absent=structuredClone(real);absent.trials[0].status='not_completed';assert.throws(()=>purpose.validateReceipt(absent));
  const partial=structuredClone(real);partial.trials[0]={id:partial.trials[0].id,seed:partial.seed_plan[0],status:'not_completed',category:'not_completed',source:'execution-plan.json',evidence_refs:['execution-plan.json']};partial.completed=0;partial.completion='cancelled_partial';assert.equal(purpose.validateReceipt(partial).requested,1);
  const state=new purpose.State();let token=state.begin();state.complete(token,real);const history=state.snapshot();state.switchMode('create');state.switchMode('review');assert.deepEqual(state.snapshot().history,history.history);
  console.log('Independent job UI checks passed: actual CPU receipt, fixed seeds, burst guard, lost-response retry, pagehide/revisit, cancellation versus termination, stale poll, source/plan mismatches, partial denominator, mode/history isolation. No HTTP.');
})().catch(e=>{console.error(e);process.exitCode=1;});
