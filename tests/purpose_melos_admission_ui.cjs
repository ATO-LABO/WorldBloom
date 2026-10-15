const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const api=require('../viewer/static/purpose-workspace.js');
const digest=s=>crypto.createHash('sha256').update(s).digest('hex');
const H='a'.repeat(64),H2='b'.repeat(64);
function fixture(){
 const r=structuredClone(JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/purpose_common_run_records.json'))).records[0]);
 const provider={adapter:'melos_ninegene_choices_v1',schema_version:1,source_hash:H,config_hash:H,table_hash:null,rng_owner:'provider'};
 const catalog={'0':{provider,initial_hash:H},'63':{provider:{...provider,config_hash:H2},initial_hash:H2}};
 const identity=id=>({id,version:1,source_hash:H,config_hash:H});
 const plan={schema_version:1,execution_kind:'independent_trials',sampler:identity('melos_finite_session_v1'),provider:{...identity('melos_predeclared_seed_providers_v1'),config_hash:digest(api.canonical(catalog))},classification:identity('melos_physical_evidence_v1'),predeclared_seeds:[0,63],planned_trials:2,selection:{fitness_selection:false,breeding:false,keep_filter:false,canon_feedback:false},observation:{unit:'minute',start:0,end:4320,stop_contract_hash:H}};
 Object.assign(r,{descriptor_scope:'melos_committed_session_v1',verified_execution:null,time_axis:{unit:'minute',start:0,end:4320},execution_plan:plan,execution_plan_hash:digest(api.canonical(plan)),provider_identity:{...provider,runtime_source_hash:H},input_hashes:{seed:0,files:{}},observations:{scenario_finished:false,status:'unresolved',physical_court_arrival:false,calendar_horizon_reached:false,observed_minute:r.observed_through,calendar_horizon:4320,fixed_two_minute_report:{completed:false},completed_120_minute_wedding:{completed:false}}});
 r.melos_admission={plan_hash:r.execution_plan_hash,seed:0,aggregate_provider:plan.provider,actual_provider:provider,initial_session_hash:H,provider_catalog:catalog};return r;
}
(async()=>{
 const specAPI=require('../viewer/static/purpose-independent.js');const spec=specAPI.melosProfileSpec();assert.equal(spec.execution_profile.id,'melos_approved_ninegene_v1');spec.execution_profile.genome.category_weight.I=1;assert.equal(specAPI.melosProfileSpec().execution_profile.genome.category_weight.I,0.5);
 const raw=fixture(),v=await api.verifyRunRecord(raw,digest);assert.equal(v.plan_verification,'matched');assert.deepEqual(api.recordIntegrity(raw).errors,[]);assert.ok(api.recordIntegrity(raw).unchecked.length);
 for(const mutate of [r=>delete r.melos_admission.provider_catalog['63'],r=>r.melos_admission.seed=63,r=>r.provider_identity.config_hash=H2,r=>r.melos_admission.initial_session_hash=H2,r=>r.verified_execution={},r=>r.descriptor_scope='unknown']){const r=fixture();mutate(r);assert.throws(()=>api.validateRunRecord(r));}
 const forged=fixture();forged.melos_admission.provider_catalog['63'].initial_hash=H;await assert.rejects(()=>api.verifyRunRecord(forged,digest),/catalog.*hash/);
 const verified=await api.verifyRunRecord(fixture(),digest);verified.record.melos_admission.provider_catalog['63'].initial_hash=H;
 const state=new api.State();state.complete(state.begin(),verified);assert.equal(state.history[0].plan_verification,'unchecked');
 const completed=fixture();completed.observations={...completed.observations,scenario_finished:true,status:'unresolved',scenario_outcome:null};
 const receipt={schema:'worldbloom_independent_v2',schema_version:2,id:'job-fixture',world:'fixture',method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,world_revision:H,policy_revision:H,engine_revision:H,settings_version:H,execution_plan_sha256:completed.execution_plan_hash,execution_plan_id:completed.execution_plan_hash,sampling_rule:'melos_finite_session_v1',classification_rule:'melos_physical_evidence_v1',source:'saved fixture',knowledge_scope:'actorview',seed_plan:[0,63],requested:2,completed:1,completion:'partial',trials:[{id:'trial-0000',seed:0,status:'observed',category:'unresolved',source:'record',evidence_refs:['layers'],run_record:completed},{id:'trial-0001',seed:63,status:'not_completed',category:'not_completed',source:'plan',evidence_refs:['plan']}]};
 await api.verifyRunRecord(receipt,digest);
 assert.deepEqual(api.melosObservationCounts(receipt.trials),{physical_arrival:0,report_completed:0,wedding_completed:0,missing_records:1});
 for(const status of ['execution_failure','not_completed']){
  const partial=structuredClone(receipt);partial.trials[0].status=status;partial.trials[0].category=status;partial.trials[0].run_record.observations.scenario_finished=false;partial.completed=status==='execution_failure'?1:0;
  await api.verifyRunRecord(partial,digest);
  const changed=structuredClone(partial);changed.trials[0].run_record.melos_admission.provider_catalog['63'].initial_hash=H;await assert.rejects(()=>api.verifyRunRecord(changed,digest),/catalog.*hash/);
  const wrongSeed=structuredClone(partial);wrongSeed.trials[0].run_record.melos_admission.seed=63;assert.throws(()=>api.validateReceipt(wrongSeed));
 }
 const paused=structuredClone(receipt);paused.trials[0].run_record.observations.scenario_finished=false;assert.throws(()=>api.validateReceipt(paused));
 const wrongCategory=structuredClone(receipt);wrongCategory.trials[0].category='success';assert.throws(()=>api.validateReceipt(wrongCategory));
 console.log('Melos catalog UI checks passed: per-seed membership, full catalog hash, detached revision checks; synthetic contract fixture only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
