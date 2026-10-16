const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const api=require('../viewer/static/purpose-workspace.js');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_common_run_records.json'),'utf8'));
const digest=s=>crypto.createHash('sha256').update(s,'utf8').digest('hex');
(async()=>{
  assert.equal(fixture.fixture_only,true);
  for(const raw of fixture.records){
    const r=await api.verifyRunRecord(raw,digest);
    assert.equal(r.kind,'common_run_record');assert.equal(r.record.actual_terminal,null);
    assert.equal(r.record.stop_reason,'paused');assert.equal(r.record.observed_through,4);
    assert.equal(r.record.execution_plan_hash,digest(api.canonical(r.record.execution_plan)));
    const state=new api.State();const token=state.begin();state.complete(token,r);
    state.switchMode('create');state.switchMode('review');
    assert.equal(api.canonical(state.history[0].record),api.canonical(raw));
    const restored=new api.State(JSON.parse(JSON.stringify(state.snapshot())));
    assert.deepEqual(restored.history[0].record,raw);
    assert.equal(state.history[0].plan_verification,'matched');assert.equal(restored.history[0].plan_verification,'unchecked');
    assert.throws(()=>api.summarize(r)); // A single RunRecord is not a classified trial batch.
  }
  const mutate=fn=>{const r=structuredClone(fixture.records[0]);fn(r);return r;};
  const changedHash=await api.verifyRunRecord(fixture.records[0],digest);
  changedHash.record.execution_plan_hash='0'.repeat(64);
  const changedState=new api.State();changedState.complete(changedState.begin(),changedHash);
  assert.equal(changedState.history[0].plan_verification,'unchecked');
  await assert.rejects(()=>api.verifyRunRecord(changedHash.record,digest),/hash/);
  for(const fn of [r=>r.schema_version=2,r=>r.execution_plan.execution_kind='ui_review',r=>r.execution_plan.selection.canon_feedback=true,
    r=>r.execution_plan.planned_trials=100,r=>r.execution_plan.predeclared_seeds=[7,7],r=>r.time_axis.unit='hours',r=>r.observed_through=11,
    r=>r.execution_plan_hash='bad',r=>r.ledger.sha256='bad',r=>r.actual_terminal={verb:'ending',line:0,event_id:null,details:{}},
    r=>r.execution_plan.provider.version=0,r=>delete r.execution_plan.classification.config_hash])
    assert.throws(()=>api.validateRunRecord(mutate(fn)));
  await assert.rejects(()=>api.verifyRunRecord(mutate(r=>r.execution_plan.sampler.id='changed'),digest),/hash/);
  const legacyNoPlan=mutate(r=>{r.execution_plan=null;r.execution_plan_hash=null;r.time_contract_hashes=null;r.provider_identity=null;});
  assert.equal((await api.verifyRunRecord(legacyNoPlan,digest)).record.execution_plan,null);
  assert.throws(()=>api.validateRunRecord(mutate(r=>r.execution_plan=null)));
  assert.deepEqual(api.validateRunRecord(mutate(r=>r.configured_target_ending=['goal-a','goal-b'])).record.configured_target_ending,['goal-a','goal-b']);
  const ended=mutate(r=>{r.actual_terminal={verb:'ending',line:33,event_id:'fixture-ending',details:{observed:'different ending'}};r.stop_reason='ending';});
  const result=await api.verifyRunRecord(ended,digest);
  assert.notEqual(result.record.actual_terminal.details.observed,result.record.configured_target_ending);
  const source=fs.readFileSync(path.resolve(__dirname,'../viewer/static/purpose-workspace.js'),'utf8');
  assert.ok(source.includes('この単回記録から独立試行の結末分布を集計しません'));
  assert.ok(source.includes('設定上の目標結末'));assert.ok(source.includes('実際の終端'));assert.ok(source.includes('時間契約の意味hash'));assert.ok(source.includes('時間契約の表示hash'));
  const inconsistent=mutate(r=>{r.input_hashes.seed=99;r.time_axis.unit='minute';});
  assert.equal(api.recordIntegrity(api.validateRunRecord(inconsistent).record).errors.length,2);
  const providerMismatch=mutate(r=>r.provider_identity={adapter:'other',schema_version:1,source_hash:r.execution_plan.provider.source_hash,config_hash:r.execution_plan.provider.config_hash});
  assert.ok(api.recordIntegrity(providerMismatch).errors.some(t=>t.includes('provider')));
  const caller=structuredClone(fixture.records[0]);caller.observations.nested={value:1};
  const isolated=api.validateRunRecord(caller);caller.observations.nested.value=99;assert.equal(isolated.record.observations.nested.value,1);
  console.log('Common RunRecord/ExecutionPlan UI compatibility checks passed: 4 kinds, canonical hashes, distinct terminal/target, unaggregated single records, mode/revisit retention. Contract fixtures only; zero engine runs.');
})().catch(e=>{console.error(e);process.exitCode=1;});
