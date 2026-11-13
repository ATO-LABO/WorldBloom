// Synthetic read-only descriptor contracts. Not native OS, browser-layout or LLM evidence.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const api=require('../viewer/static/purpose-workspace.js');
const digest=s=>crypto.createHash('sha256').update(s).digest('hex'),H='a'.repeat(64),H2='b'.repeat(64),stream='c'.repeat(32),publication='d'.repeat(32);
const clone=v=>JSON.parse(JSON.stringify(v));
function selection(ordinal=0){return {leaf:`wbgen-${stream}-${String(ordinal).padStart(20,'0')}-${H}.wbg`,expected:{sha256:H,size:1000,stream_id:stream,ordinal}};}
function artifact(role){return {leaf:`wbexport-${stream}-${'0'.repeat(20)}-${H}-${publication}-${role}`,sha256:H,size:100};}
function fixture(){
  const r=clone(JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_common_run_records.json'))).records[0]);
  const provider={adapter:'melos_ninegene_choices_v1',schema_version:1,source_hash:H,config_hash:H,table_hash:null,rng_owner:'provider'};
  const catalog={'0':{provider,initial_hash:H},'63':{provider:{...provider,config_hash:H2},initial_hash:H2}};
  const identity=id=>({id,version:1,source_hash:H,config_hash:H});
  const plan={schema_version:1,execution_kind:'independent_trials',sampler:identity('melos_finite_session_v1'),provider:{...identity('melos_predeclared_seed_providers_v1'),config_hash:digest(api.canonical(catalog))},classification:identity('melos_physical_evidence_v1'),predeclared_seeds:[0,63],planned_trials:2,selection:{fitness_selection:false,breeding:false,keep_filter:false,canon_feedback:false},observation:{unit:'minute',start:0,end:4320,stop_contract_hash:H}};
  Object.assign(r,{descriptor_scope:'melos_native_generation_v1',storage:{kind:'native_generation_exports_v1',selected_generation:selection(),publication_id:publication,restart_authority:false},verified_execution:null,independent_ui_verified:false,native_ui_admission:'not_connected',generation_sha256:H,checkpoint_sha256:H2,session_state_sha256:H2,result_sha256:H,saved_revision_hash:H,ledger:artifact('layers.jsonl'),result:artifact('result.json'),time_axis:{unit:'minute',start:0,end:4320},execution_plan:plan,execution_plan_hash:digest(api.canonical(plan)),provider_identity:{...provider,runtime_source_hash:H},input_hashes:{seed:0,files:{}},observations:{scenario_finished:true,status:'unresolved',physical_court_arrival:false,calendar_horizon_reached:false,observed_minute:r.observed_through,calendar_horizon:4320,fixed_two_minute_report:{completed:false},completed_120_minute_wedding:{completed:false}}});
  r.melos_admission={plan_hash:r.execution_plan_hash,seed:0,aggregate_provider:plan.provider,actual_provider:provider,initial_session_hash:H,provider_catalog:catalog};
  return {schema:'worldbloom_independent_v3',schema_version:3,storage:{kind:'melos_independent_native_v1',restart_authority:false},execution_profile:'melos_approved_ninegene_v1',id:'job-native-fixture',world:'melos-input',method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,world_revision:H,policy_revision:H,engine_revision:H,settings_version:H,execution_plan_sha256:r.execution_plan_hash,execution_plan_id:r.execution_plan_hash,sampling_rule:'melos_finite_session_v1',classification_rule:'melos_physical_evidence_v1',source:'synthetic native fixture',knowledge_scope:'actorview',seed_plan:[0,63],requested:2,completed:1,failed:0,unresolved:1,completion:'partial',trials:[{id:'trial-0000',seed:0,status:'observed',category:'unresolved',source:artifact('run-record.json').leaf,evidence_refs:[r.ledger.leaf,r.result.leaf,artifact('run-record.json').leaf],selected_generation:selection(),artifacts:{layers:r.ledger,result:r.result,run_record:artifact('run-record.json')},run_record:r},{id:'trial-0001',seed:63,status:'not_completed',category:'not_completed',source:'plan',evidence_refs:['plan']}]};
}
(async()=>{
  const raw=fixture(),record=raw.trials[0].run_record;
  assert.equal((await api.verifyRunRecord(record,digest)).plan_verification,'matched');
  await api.verifyRunRecord(raw,digest);
  assert.deepEqual(api.melosObservationCounts(raw.trials),{physical_arrival:0,report_completed:0,wedding_completed:0,missing_records:1});
  assert.ok(api.recordIntegrity(record).unchecked.length);assert.deepEqual(api.recordIntegrity(record).errors,[]);
  const invalid=[
    r=>r.completion='failed_partial',r=>r.operation_error={phase:'export',error_type:'StoreError',error:'unexpected on partial'},
    r=>r.schema_version=2,r=>r.storage.restart_authority=true,r=>r.storage.kind='legacy',r=>r.storage.path='inferred',r=>delete r.execution_profile,
    r=>delete r.trials[0].selected_generation,r=>delete r.trials[0].artifacts,
    r=>r.trials[0].selected_generation.leaf='../checkpoint.wbg',r=>r.trials[0].selected_generation.expected.ordinal=1,
    r=>r.trials[0].selected_generation.expected.size=true,r=>r.trials[0].selected_generation.expected.size=33556501,
    r=>r.trials[0].selected_generation.expected.ordinal=Number.MAX_SAFE_INTEGER+1,
    r=>r.trials[0].artifacts.layers.leaf='layers.jsonl',r=>r.trials[0].artifacts.run_record.sha256='bad',
    r=>r.trials[0].artifacts.run_record.size=-1,r=>r.trials[0].artifacts.run_record.leaf=r.trials[0].artifacts.layers.leaf,
    r=>r.trials[0].artifacts.run_record.path='alias',r=>r.trials[0].source='guessed/path',r=>r.trials[0].evidence_refs=['guessed/path'],
    r=>r.trials[0].run_record.storage.kind='legacy',r=>r.trials[0].run_record.storage.restart_authority=true,
    r=>r.trials[0].run_record.storage.publication_id='e'.repeat(32),r=>r.trials[0].run_record.generation_sha256=H2,
    r=>r.trials[0].run_record.result_sha256=H2,r=>r.trials[0].run_record.ledger.path='layers.jsonl',
    r=>r.trials[0].run_record.native_ui_admission='verified',r=>r.trials[0].run_record.independent_ui_verified=true,
    r=>r.trials[0].run_record.verified_execution={},r=>r.trials[0].run_record.descriptor_scope='melos_committed_session_v1',
    r=>r.trials[0].run_record.provider_identity.config_hash=H2,r=>r.trials[0].run_record.melos_admission.seed=63,
    r=>r.trials[1].selected_generation=selection(),r=>r.trials[1].artifacts={},r=>r.trials[1].last_accepted_generation={leaf:'guess'},
    r=>r.trials[1].operation_error={phase:'export',error_type:'StoreError'},r=>r.operation_error={phase:'export',error:'missing type'},r=>r.completed=2,r=>r.failed=1,r=>r.unresolved=0,
    r=>{r.trials[0].status=r.trials[0].category='execution_failure';r.trials[0].run_record.observations.scenario_finished=false;},
    r=>{r.schema='worldbloom_independent_v2';r.schema_version=2;delete r.storage;},
  ];
  for(const mutate of invalid){const bad=fixture();mutate(bad);assert.throws(()=>api.validateReceipt(bad),undefined,mutate.toString());}
  const partial=fixture();partial.completion='failed_partial';partial.completed=0;partial.unresolved=0;partial.trials[0].status=partial.trials[0].category='not_completed';partial.trials[0].run_record.observations.scenario_finished=false;
  partial.trials[0].last_accepted_generation=selection(1);partial.trials[0].operation_error={phase:'export',error_type:'StoreError',error:'synthetic export failure'};
  partial.operation_error={phase:'export',error_type:'StoreError',error:'synthetic export failure <literal>'};
  partial.trials[1].last_accepted_generation=selection(2);
  await api.verifyRunRecord(partial,digest);assert.equal(api.melosObservationCounts(partial.trials).missing_records,1);
  const forged=fixture();forged.trials[0].run_record.melos_admission.provider_catalog['63'].initial_hash=H;await assert.rejects(()=>api.verifyRunRecord(forged,digest),/catalog.*hash/);
  const restored=new api.State({history:[raw]});assert.equal(restored.history[0].schema,'worldbloom_independent_v3');
  const legacy=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_independent_real_result.json')));await api.verifyRunRecord(legacy,digest);
  const {environment}=require('./purpose_dom_ui.cjs'),e=environment();e.context.crypto=crypto.webcrypto;e.context.TextEncoder=TextEncoder;e.context.Uint8Array=Uint8Array;
  assert.equal(await e.context.WorldBloomPurpose.acceptSavedResult(raw),true);
  assert.match(e.fields.result.textContent,/母数 2件、処理済み 1件/);assert.match(e.fields.result.textContent,/独立した実動作検証は別/);
  assert.equal(e.stored().history[0].schema,'worldbloom_independent_v3');
  const bad=fixture();bad.trials[0].source='wrong';await assert.rejects(()=>e.context.WorldBloomPurpose.acceptSavedResult(bad));assert.equal(e.stored().history.length,1);
  const single=environment();single.context.crypto=crypto.webcrypto;single.context.TextEncoder=TextEncoder;single.context.Uint8Array=Uint8Array;
  assert.equal(await single.context.WorldBloomPurpose.acceptSavedResult(record),true);assert.ok(single.fields.result.textContent.includes(record.ledger.leaf));assert.ok(!single.fields.result.textContent.includes('undefined / SHA-256'));
  assert.equal(await single.context.WorldBloomPurpose.acceptSavedResult(partial),true);assert.match(single.fields.result.textContent,/母数 2件、処理済み 0件/);assert.match(single.fields.result.textContent,/synthetic export failure <literal>/);
  console.log('Native generation UI checks passed: strict v3/reference bindings, mixed-format rejection, partial export failure counts, legacy reading, native DOM/history, no filesystem inference. Synthetic descriptors only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
