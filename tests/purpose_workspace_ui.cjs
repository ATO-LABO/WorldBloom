const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {validate,summarize,State}=require('../viewer/static/purpose-workspace.js');
const receipt=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/melos-independent100-review-v1.json'),'utf8'));
const r=validate(receipt), s=summarize(r);
const crypto=require('node:crypto');
const planBytes=fs.readFileSync(path.join(__dirname,'fixtures/melos-independent100-execution-plan-v1.json'));
const plan=JSON.parse(planBytes);
assert.equal(crypto.createHash('sha256').update(planBytes).digest('hex'),r.execution_plan_sha256);
assert.equal(plan.id,r.execution_plan_id);assert.equal(plan.execution_kind,'independent_trials');
assert.equal(plan.selection_enabled,false);assert.equal(plan.breeding_enabled,false);assert.equal(plan.canon_feedback_enabled,false);
assert.deepEqual(plan.seed_plan,r.seed_plan);assert.equal(plan.trials_count,r.trials.length);
for(const key of ['sampler_version','provider_version','classification_version','observation_scope','input_hashes'])assert.ok(plan[key]);
assert.equal(s.total,100);assert.equal(s.matrix.in_time.attended,3);assert.equal(s.matrix.in_time.not_attended,97);
assert.deepEqual(s.representatives.map(t=>t.seed).sort((a,b)=>a-b),[0,27]);
assert.deepEqual(s.outcomes.map(x=>x[1]).sort((a,b)=>a-b),[32,68]);
const change=(mutate)=>{const x=structuredClone(receipt);mutate(x);return x;};
for(const mutate of [x=>x.method='ga',x=>x.population='elites',x=>x.selection='fitness',x=>x.reference_guided=true,
  x=>x.trials.pop(),x=>x.trials[1].seed=0,x=>x.seed_plan.push(100),x=>x.trials[0].return_status='success',
  x=>x.trials[0].wedding_status='unknown',x=>x.trials[0].evidence_ids=[],x=>x.policy_revision='',x=>x.seed_plan[0]=NaN])
  assert.throws(()=>validate(change(mutate)));
const incomplete=validate(change(x=>{x.trials[0].return_status='unresolved';x.trials[0].wedding_status='unknown';x.trials[1].return_status='execution_failure';}));
assert.equal(summarize(incomplete).total,100);assert.equal(summarize(incomplete).matrix.unresolved.unknown,1);
assert.equal(summarize(incomplete).matrix.execution_failure.not_attended,1);
const state=new State();state.world='melos';state.note='帰還と生存を分ける';
const first=state.begin();assert.equal(state.begin(),null);state.switchMode('review');
assert.equal(state.complete(first,r),true);state.switchMode('create');assert.equal(state.history.length,1);
assert.equal(state.world,'melos');assert.equal(state.note,'帰還と生存を分ける');
const second=state.begin();state.cancel();assert.equal(state.complete(second,r),false);
const third=state.begin();assert.throws(()=>state.complete(third,{}));state.cancel();assert.equal(state.history.length,1);
const fourth=state.begin();state.cancel();const fifth=state.begin();assert.equal(state.complete(fourth,r),false);
assert.equal(state.complete(fifth,r),true);
const back=new State(JSON.parse(JSON.stringify(state.snapshot())));assert.equal(back.world,'melos');assert.equal(back.history.length,1);
back.switchMode('review');back.switchMode('create');assert.equal(back.history[0].id,r.id);
assert.equal(new State({history:[{}],mode:'invalid'}).history.length,0);
for(let i=1;i<=4;i++){const t=back.begin();back.complete(t,change(x=>x.id='result-'+i));}
assert.equal(back.history.length,5);
const conflict=back.begin();assert.throws(()=>back.complete(conflict,change(x=>{x.id='result-1';x.world='別の世界';})));back.cancel();
assert.equal(back.history.length,5);
console.log('Purpose contract/state checks passed; historical 100-trial classification fixture verified.');
