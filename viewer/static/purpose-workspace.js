/* UI-only purpose and immutable review receipts. No engine or job calls. */
(function (global) {
  'use strict';
  const MODES = ['create', 'review'];
  const RETURNS = ['in_time', 'late', 'no_return_observed', 'unresolved', 'execution_failure'];
  const WEDDINGS = ['attended', 'not_attended', 'unknown'];
  const LABEL = {in_time:'期限内帰還',late:'遅延帰還',no_return_observed:'未帰還（観測終了まで）',
    unresolved:'未確定',execution_failure:'実行失敗',attended:'婚礼参加',not_attended:'婚礼不参加',unknown:'婚礼判定不明'};
  const KEY = 'worldbloom-purpose-v1';
  const MAX_BYTES = 16 * 1024 * 1024;
  const EXECUTION_KINDS={production_search:'制作の候補探索',independent_trials:'独立試行',explicit_witness:'明示した行動の実行',replay:'再生'};
  const verifiedPlans=new WeakMap();
  function fail(message) { throw new Error(message); }
  function string(value, name, limit=4000) {
    if (typeof value !== 'string' || !value.trim() || value.length > limit) fail(name+'の記録が不正です。');
    return value;
  }
  function validate(raw) {
    if (!raw || raw.schema !== 'worldbloom_review_v1') fail('レビュー結果の形式が違います。');
    if (raw.method !== 'independent_trials' || raw.population !== 'unselected' || raw.selection !== 'none'
      || raw.reference_guided !== false) fail('GA選別群や原作への誘導を含む結果は独立試行として読めません。');
    const out = {schema:raw.schema,method:raw.method,population:raw.population,selection:raw.selection,reference_guided:false};
    for (const key of ['id','world','world_revision','policy_revision','engine_revision','settings_version','sampling_rule',
      'classification_rule','observation_stop','source']) out[key] = string(raw[key], key);
    // Legacy saved receipts remain viewable; executable plans are separate records.
    if(raw.execution_plan_id!==undefined || raw.execution_plan_sha256!==undefined){
      out.execution_plan_id=string(raw.execution_plan_id,'execution_plan_id',200);
      out.execution_plan_sha256=string(raw.execution_plan_sha256,'execution_plan_sha256',64);
      if(!/^[a-f0-9]{64}$/.test(out.execution_plan_sha256))fail('実行計画hashが不正です。');
    }
    if (!Array.isArray(raw.seed_plan) || !raw.seed_plan.length || raw.seed_plan.length>1000 ||
      raw.seed_plan.some(v=>!Number.isSafeInteger(v) || v<0) || new Set(raw.seed_plan).size!==raw.seed_plan.length)
      fail('事前seed一覧が不正です。');
    if (!Array.isArray(raw.trials) || raw.trials.length !== raw.seed_plan.length) fail('母数と全試行の記録が一致しません。');
    out.seed_plan = [...raw.seed_plan];
    const seeds = new Set(), ids = new Set();
    out.trials = raw.trials.map(t=>{
      if (!t || !out.seed_plan.includes(t.seed) || seeds.has(t.seed)) fail('seedの重複または欠落があります。');
      seeds.add(t.seed);
      const id = string(t.id,'trial id',100);
      if (ids.has(id)) fail('試行IDが重複しています。');
      ids.add(id);
      if (!RETURNS.includes(t.return_status) || !WEDDINGS.includes(t.wedding_status) ||
        (RETURNS.indexOf(t.return_status)<3 && t.wedding_status==='unknown')) fail('帰還・婚礼の判定が不正です。');
      if (!Array.isArray(t.evidence_ids) || !t.evidence_ids.length || t.evidence_ids.length>100) fail('判定根拠がありません。');
      return {id,seed:t.seed,return_status:t.return_status,wedding_status:t.wedding_status,
        outcome:string(t.outcome,'outcome'),reason:string(t.reason,'reason'),story:string(t.story,'story',16000),
        evidence_ids:t.evidence_ids.map(e=>string(e,'evidence id',200))};
    });
    return out;
  }
  function mapping(v,name){if(!v||typeof v!=='object'||Array.isArray(v))fail(name+'の形式が不正です。');return v;}
  function integer(v,name,min=0,max=Number.MAX_SAFE_INTEGER){if(!Number.isSafeInteger(v)||v<min||v>max)fail(name+'が不正です。');return v;}
  function hash(v,name){if(typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v))fail(name+'のhashが不正です。');return v;}
  function keys(v,names,name){mapping(v,name);if(Object.keys(v).sort().join('|')!==[...names].sort().join('|'))fail(name+'の項目が中間契約と異なります。');}
  function identity(v,name){keys(v,['id','version','source_hash','config_hash'],name);string(v.id,name+' id',200);integer(v.version,name+' version',1);hash(v.source_hash,name+' source');hash(v.config_hash,name+' config');}
  function canonical(v){
    if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';
    if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
    return JSON.stringify(v);
  }
  function validateExecutionPlan(v){
    keys(v,['schema_version','execution_kind','sampler','provider','predeclared_seeds','planned_trials','selection','observation','classification'],'ExecutionPlan');
    if(v.schema_version!==1||!Object.hasOwn(EXECUTION_KINDS,v.execution_kind))fail('ExecutionPlanの版または実行種別が未対応です。');
    for(const name of ['sampler','provider','classification'])identity(v[name],name);
    if(!Array.isArray(v.predeclared_seeds)||!v.predeclared_seeds.length||v.predeclared_seeds.length>10000)fail('事前seed列が不正です。');
    v.predeclared_seeds.forEach(seed=>integer(seed,'seed'));
    integer(v.planned_trials,'planned_trials',1,100000000);
    keys(v.selection,['fitness_selection','breeding','keep_filter','canon_feedback'],'selection');
    if(Object.values(v.selection).some(flag=>typeof flag!=='boolean'))fail('選別の記録が不正です。');
    if(v.execution_kind==='independent_trials'&&(v.planned_trials!==v.predeclared_seeds.length||new Set(v.predeclared_seeds).size!==v.predeclared_seeds.length||Object.values(v.selection).some(Boolean)))fail('独立試行計画に選別・育種・原作誘導または母数不整合があります。');
    keys(v.observation,['unit','start','end','stop_contract_hash'],'observation');
    if(!['minute','logical_step'].includes(v.observation.unit))fail('観測時間の単位が未対応です。');
    integer(v.observation.start,'observation start');integer(v.observation.end,'observation end',v.observation.start);hash(v.observation.stop_contract_hash,'stop contract');
    return JSON.parse(JSON.stringify(v));
  }
  function jsonEvidence(v,depth=0){
    if(depth>16)fail('実行記録の階層が深すぎます。');
    if(v===null||typeof v==='boolean')return;
    if(typeof v==='string'){if(v.length>100000)fail('実行記録の文字列が長すぎます。');return;}
    if(typeof v==='number'){if(!Number.isFinite(v))fail('実行記録に不正な数値があります。');return;}
    if(Array.isArray(v)){v.forEach(x=>jsonEvidence(x,depth+1));return;}
    mapping(v,'実行記録');Object.values(v).forEach(x=>jsonEvidence(x,depth+1));
  }
  function isMelosRecord(r){return r.descriptor_scope==='melos_committed_session_v1';}
  function validateMelosAdmission(r){
    const p=r.execution_plan,a=r.melos_admission;
    if(!p||p.sampler.id!=='melos_finite_session_v1'||p.provider.id!=='melos_predeclared_seed_providers_v1'||p.classification.id!=='melos_physical_evidence_v1'||r.verified_execution!==null)fail('Melosの明示した実行契約が不正です。');
    keys(a,['plan_hash','seed','aggregate_provider','actual_provider','initial_session_hash','provider_catalog'],'Melos admission');
    hash(a.plan_hash,'Melos plan');hash(a.initial_session_hash,'initial session');integer(a.seed,'Melos seed');
    if(a.plan_hash!==r.execution_plan_hash||a.seed!==r.input_hashes.seed||!p.predeclared_seeds.includes(a.seed)||canonical(a.aggregate_provider)!==canonical(p.provider))fail('Melosのseedと計画の対応が不正です。');
    mapping(a.provider_catalog,'provider catalog');
    if(canonical(Object.keys(a.provider_catalog).sort())!==canonical(p.predeclared_seeds.map(String).sort()))fail('provider catalogの母数が計画と異なります。');
    const provider=v=>{keys(v,['adapter','schema_version','source_hash','config_hash','table_hash','rng_owner'],'Melos provider');if(v.adapter!=='melos_ninegene_choices_v1'||v.schema_version!==1||v.table_hash!==null||v.rng_owner!=='provider')fail('通常9遺伝子providerが不正です。');hash(v.source_hash,'provider source');hash(v.config_hash,'provider config');};
    for(const slot of Object.values(a.provider_catalog)){keys(slot,['provider','initial_hash'],'provider slot');provider(slot.provider);hash(slot.initial_hash,'initial slot');}
    provider(a.actual_provider);
    const slot=a.provider_catalog[String(a.seed)];
    if(canonical(slot.provider)!==canonical(a.actual_provider)||slot.initial_hash!==a.initial_session_hash)fail('実providerが宣言seedのcatalogと異なります。');
    const actual={...r.provider_identity};hash(actual.runtime_source_hash,'provider runtime');delete actual.runtime_source_hash;
    if(canonical(actual)!==canonical(a.actual_provider))fail('実providerと保存された拘束が異なります。');
    const o=r.observations;
    for(const k of ['scenario_finished','physical_court_arrival','calendar_horizon_reached'])if(typeof o[k]!=='boolean')fail('Melosの観測状態が不正です。');
    for(const k of ['fixed_two_minute_report','completed_120_minute_wedding'])if(!o[k]||typeof o[k].completed!=='boolean')fail('Melosの完了根拠が不正です。');
    if(o.observed_minute!==r.observed_through||o.calendar_horizon!==r.time_axis.end)fail('Melosの観測時刻が保存記録と異なります。');
    return a;
  }
  function validateRunRecord(raw){
    mapping(raw,'RunRecord');jsonEvidence(raw);
    const required=['schema_version','run_id','time_axis','observed_through','configured_target_ending','execution_plan','execution_plan_hash','ledger','actual_terminal','stop_reason','observations','provider_identity','input_hashes','run_spec_hash','time_contract_hashes','engine_hash'];
    if(required.some(k=>!Object.hasOwn(raw,k))||raw.schema_version!==1)fail('RunRecordの版または必須項目が未対応です。');
    string(raw.run_id,'run_id',200);keys(raw.time_axis,['unit','start','end'],'time_axis');
    if(!['minute','logical_step'].includes(raw.time_axis.unit))fail('時間単位が未対応です。');
    integer(raw.time_axis.start,'time start');integer(raw.time_axis.end,'time end',raw.time_axis.start);integer(raw.observed_through,'observed_through',raw.time_axis.start,raw.time_axis.end);
    if(Array.isArray(raw.configured_target_ending)){
      if(!raw.configured_target_ending.length||raw.configured_target_ending.length>1000||new Set(raw.configured_target_ending).size!==raw.configured_target_ending.length)fail('目標結末の件数または重複が不正です。');
      raw.configured_target_ending.forEach(v=>string(v,'configured_target_ending',4000));
    }else if(raw.configured_target_ending!==null)string(raw.configured_target_ending,'configured_target_ending',4000);
    mapping(raw.ledger,'ledger');string(raw.ledger.path,'ledger path',500);hash(raw.ledger.sha256,'ledger');
    hash(raw.run_spec_hash,'run_spec');if(typeof raw.engine_hash!=='string'||!/^[a-f0-9]{12,64}$/.test(raw.engine_hash))fail('engine hashが不正です。');
    string(raw.stop_reason,'stop_reason',200);mapping(raw.observations,'observations');mapping(raw.input_hashes,'input_hashes');
    if(raw.provider_identity!==null)mapping(raw.provider_identity,'provider_identity');
    if(raw.time_contract_hashes!==null){mapping(raw.time_contract_hashes,'time_contract_hashes');for(const k of ['run_spec_hash','semantic_hash','presentation_hash'])hash(raw.time_contract_hashes[k],k);}
    if(raw.execution_plan!==null){validateExecutionPlan(raw.execution_plan);hash(raw.execution_plan_hash,'ExecutionPlan');}
    else if(raw.execution_plan_hash!==null)fail('計画本体のないExecutionPlan hashは受け付けません。');
    if(raw.actual_terminal!==null){mapping(raw.actual_terminal,'actual_terminal');string(raw.actual_terminal.verb,'terminal verb',200);integer(raw.actual_terminal.line,'terminal line',1);if(raw.actual_terminal.event_id!==null)string(raw.actual_terminal.event_id,'terminal event id',500);mapping(raw.actual_terminal.details,'terminal details');}
    if(isMelosRecord(raw))validateMelosAdmission(raw);
    else if(Object.hasOwn(raw,'melos_admission'))fail('Melosの保存形式が不明です。');
    return {kind:'common_run_record',id:'run-record:'+raw.run_id+':'+raw.ledger.sha256,world:'共通実行記録',plan_verification:raw.execution_plan===null?'no_plan':'unchecked',record:JSON.parse(JSON.stringify(raw))};
  }
  function validateReceipt(raw){
    if(raw&&raw.schema==='worldbloom_independent_v2')return validateIndependent(raw);
    if(raw&&raw.kind==='common_run_record')return validateRunRecord(raw.record);
    if(raw&&raw.schema_version===1&&Object.hasOwn(raw,'run_id'))return validateRunRecord(raw);
    return validate(raw);
  }
  function validateIndependent(raw){
    mapping(raw,'independent receipt');jsonEvidence(raw);
    if(raw.schema!=='worldbloom_independent_v2'||raw.schema_version!==2||raw.method!=='independent_trials'||raw.population!=='unselected'||raw.selection!=='none'||raw.reference_guided!==false)fail('独立試行の実行種別が不正です。');
    for(const k of ['id','world','sampling_rule','classification_rule','source','knowledge_scope'])string(raw[k],k);
    for(const k of ['world_revision','policy_revision','engine_revision','settings_version','execution_plan_sha256'])hash(raw[k],k);
    if(raw.execution_plan_id!==raw.execution_plan_sha256)fail('計画IDとhashが一致しません。');
    if(!Array.isArray(raw.seed_plan)||!raw.seed_plan.length||raw.seed_plan.length>100||new Set(raw.seed_plan).size!==raw.seed_plan.length)fail('独立試行の事前seed列が不正です。');
    raw.seed_plan.forEach(s=>integer(s,'seed',0,2147483647));
    if(Object.hasOwn(raw,'execution_profile')&&raw.execution_profile!=='melos_approved_ninegene_v1')fail('独立試行profileが未対応です。');
    if(!Array.isArray(raw.trials)||raw.trials.length!==raw.seed_plan.length||raw.requested!==raw.seed_plan.length)fail('全試行と母数が一致しません。');
    if(!['complete','partial','cancelled_partial'].includes(raw.completion))fail('完了区分が不正です。');
    integer(raw.completed,'completed',0,raw.requested);
    const ids=new Set();let completed=0;
    raw.trials.forEach((t,i)=>{
      mapping(t,'trial');string(t.id,'trial id',100);if(ids.has(t.id)||t.seed!==raw.seed_plan[i])fail('試行IDまたはseedの対応が不正です。');ids.add(t.id);
      if(!['observed','execution_failure','not_completed'].includes(t.status))fail('試行状態が不正です。');
      string(t.category,'category',200);string(t.source,'trial source',500);
      if(!Array.isArray(t.evidence_refs)||!t.evidence_refs.length||t.evidence_refs.length>100)fail('試行の根拠がありません。');t.evidence_refs.forEach(e=>string(e,'evidence',500));
      if(t.status!=='not_completed')completed++;
      if(t.status==='observed'&&!Object.hasOwn(t,'run_record'))fail('完了試行の保存記録がありません。');
      if(Object.hasOwn(t,'run_record')){
        validateRunRecord(t.run_record);
        const r=t.run_record;
        if(raw.execution_profile==='melos_approved_ninegene_v1'&&!isMelosRecord(r))fail('profileと試行保存形式が異なります。');
        if(r.execution_plan_hash!==raw.execution_plan_sha256||!r.execution_plan||r.execution_plan.execution_kind!=='independent_trials'||canonical(r.execution_plan.predeclared_seeds)!==canonical(raw.seed_plan)||r.execution_plan.planned_trials!==raw.requested)fail('試行の計画と独立試行全体が一致しません。');
        if(isMelosRecord(r)){if(validateMelosAdmission(r).seed!==t.seed)fail('Melosの実seedが試行と異なります。');
          const o=r.observations,category=o.status==='observed'?o.scenario_outcome:o.status==='unresolved'?'unresolved':null;
          if(t.status==='observed'&&(o.scenario_finished!==true||category===null||t.category!==category))fail('Melosの完了状態と試行区分が異なります。');
          if(t.status==='not_completed'&&o.scenario_finished!==false)fail('完了したMelos記録を未完了にはできません。');}
        else {const actual=r.verified_execution;
        if(!actual||actual.seed!==t.seed||actual.execution_kind!=='independent_trials'||canonical(actual.provider)!==canonical(r.execution_plan.provider))fail('実seedまたはproviderの拘束記録が不正です。');}
      }
    });
    if(completed!==raw.completed||(raw.completion==='complete'&&completed!==raw.requested))fail('完了数と試行状態が一致しません。');
    return JSON.parse(JSON.stringify(raw));
  }
  async function verifyRunRecord(raw,digestText){
    const normalized=validateReceipt(raw);
    if(normalized.schema==='worldbloom_independent_v2'){
      for(const t of normalized.trials)if(Object.hasOwn(t,'run_record'))await verifyRunRecord(t.run_record,digestText);
      return normalized;
    }
    if(normalized.kind!=='common_run_record')return normalized;
    const r=normalized.record;if(r.execution_plan===null)return normalized;
    const digest=digestText||async function(value){
      if(!global.crypto||!global.crypto.subtle||typeof TextEncoder==='undefined')fail('このブラウザでは共通実行計画のhashを確認できません。');
      const bytes=await global.crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
      return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
    };
    if(await digest(canonical(r.execution_plan))!==r.execution_plan_hash)fail('RunRecordとExecutionPlanのhashが一致しません。');
    if(isMelosRecord(r)&&await digest(canonical(validateMelosAdmission(r).provider_catalog))!==r.execution_plan.provider.config_hash)fail('provider catalogと計画のhashが一致しません。');
    normalized.plan_verification='matched';verifiedPlans.set(normalized,{canonical:canonical(r.execution_plan),hash:r.execution_plan_hash,admission:isMelosRecord(r)?canonical(r.melos_admission):null});
    return normalized;
  }
  function recordIntegrity(record){
    const errors=[],unchecked=[];const p=record.execution_plan;
    if(p){
      if(p.observation.unit!==record.time_axis.unit||p.observation.start!==record.time_axis.start||p.observation.end!==record.time_axis.end)errors.push('計画の観測範囲と記録の時間軸が一致しません。');
      if(Object.hasOwn(record.input_hashes,'seed')){if(!p.predeclared_seeds.includes(record.input_hashes.seed))errors.push('実seedが事前seed列に含まれていません。');}
      else unchecked.push('実seedの項目がありません。');
      const identity=record.provider_identity;
      if(isMelosRecord(record)){try{validateMelosAdmission(record);}catch(error){errors.push(error.message);}}
      else if(identity){
        const pairs=[['id','adapter'],['version','schema_version'],['source_hash','source_hash'],['config_hash','config_hash']];
        if(pairs.some(([,key])=>!Object.hasOwn(identity,key)))unchecked.push('実providerの識別項目が不足しています。');
        else if(pairs.some(([planKey,runKey])=>p.provider[planKey]!==identity[runKey]))errors.push('計画と記録のprovider識別が一致しません。');
      }else unchecked.push('実providerの識別記録がありません。');
    }else unchecked.push('ExecutionPlanの記録がありません。');
    unchecked.push('ledger・checkpointの現物、保存ログと実状態の一致は未照合です。');
    return {errors,unchecked};
  }
  function melosObservationCounts(trials){
    const result={physical_arrival:0,report_completed:0,wedding_completed:0,missing_records:0};
    for(const t of trials){if(!t.run_record||!isMelosRecord(t.run_record)){result.missing_records++;continue;}
      validateMelosAdmission(t.run_record);const o=t.run_record.observations;
      result.physical_arrival+=Number(o.physical_court_arrival);result.report_completed+=Number(o.fixed_two_minute_report.completed);result.wedding_completed+=Number(o.completed_120_minute_wedding.completed);}
    return result;
  }
  function summarize(report) {
    if(!report||!Array.isArray(report.trials))fail('単回実行記録は独立試行の分布として集計できません。');
    const matrix = Object.fromEntries(RETURNS.map(r=>[r,Object.fromEntries(WEDDINGS.map(w=>[w,0]))]));
    const groups = new Map(), outcomes = new Map();
    for (const t of report.trials) {
      matrix[t.return_status][t.wedding_status]++;
      const key=t.return_status+'/'+t.wedding_status;
      if (!groups.has(key) || t.seed<groups.get(key).seed) groups.set(key,t);
      outcomes.set(t.outcome,(outcomes.get(t.outcome)||0)+1);
    }
    return {matrix,representatives:[...groups.values()],outcomes:[...outcomes],total:report.trials.length};
  }
  class State {
    constructor(saved={}) {
      this.mode=MODES.includes(saved.mode)?saved.mode:'create';
      this.world=typeof saved.world==='string'?saved.world:'';
      this.note=typeof saved.note==='string'?saved.note.slice(0,3000):'';
      this.history=[];
      for (const r of Array.isArray(saved.history)?saved.history:[]) {try {this.history.push(validateReceipt(r));} catch (_) {}}
      this.selected=Math.max(0,Math.min(Number.isInteger(saved.selected)?saved.selected:0,this.history.length-1));
      this.generation=0; this.busy=false;
    }
    switchMode(mode) {if (MODES.includes(mode)) this.mode=mode;}
    begin() {if(this.busy) return null; this.busy=true;return ++this.generation;}
    cancel() {this.busy=false;this.generation++;}
    complete(token,raw) {
      if (!this.busy || token!==this.generation) return false;
      const report=validateReceipt(raw); // caller keeps previous results when validation fails
      const verified=verifiedPlans.get(raw);
      if(report.kind==='common_run_record'&&verified&&verified.canonical===canonical(report.record.execution_plan)&&verified.hash===report.record.execution_plan_hash&&verified.admission===(isMelosRecord(report.record)?canonical(report.record.melos_admission):null))report.plan_verification='matched';
      const previous=this.history.find(r=>r.id===report.id);
      if(previous && canonical(previous.kind==='common_run_record'?previous.record:previous)!==canonical(report.kind==='common_run_record'?report.record:report)) fail(report.kind==='common_run_record'?'同じrun_idとledger hashに異なる状態が含まれています。保存revisionと台帳の対応を確認してください。':'同じ結果IDに異なる記録が含まれています。別の結果IDを確認してください。');
      this.history=[report,...this.history.filter(r=>r.id!==report.id)];
      this.selected=0;this.busy=false;return true;
    }
    snapshot() {return {mode:this.mode,world:this.world,note:this.note,history:this.history,selected:this.selected};}
  }
  const api={validate,validateReceipt,validateIndependent,validateRunRecord,validateExecutionPlan,verifyRunRecord,recordIntegrity,canonical,summarize,melosObservationCounts,State,MAX_BYTES};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  global.WorldBloomPurpose=api;
  if (typeof document==='undefined') return;
  let saved={}, storageOK=true;
  try {saved=JSON.parse(sessionStorage.getItem(KEY)||'{}')||{};} catch (_) {storageOK=false;}
  const state=new State(saved), root=document.querySelector('[data-purpose-root]');
  const q=s=>root&&root.querySelector(s);
  const status=q('[data-purpose-status]'), result=q('[data-purpose-result]'), history=q('[data-purpose-history]');
  const file=q('[data-purpose-file]'), importButton=q('[data-purpose-import]'), cancelButton=q('[data-purpose-cancel]');
  const copyButton=q('[data-purpose-copy]'),copyStatus=q('[data-purpose-copy-status]'),copyFallback=q('[data-purpose-copy-fallback]');
  let copyEpoch=0;
  let reader=null;
  function persist() {
    try {
      // Other pages own only purpose; an old BFCache page must not erase receipts.
      const fresh=JSON.parse(sessionStorage.getItem(KEY)||'{}')||{};
      const value=root?state.snapshot():{...fresh,mode:state.mode};
      sessionStorage.setItem(KEY,JSON.stringify(value));storageOK=true;
    } catch (_) {storageOK=false;}
    const el=q('[data-purpose-storage]');
    if(el) el.textContent=storageOK?'':'ブラウザ内の保存が使えません。入力と結果はこの画面で保持しますが、再訪時は復元できません。';
    for(const notice of document.querySelectorAll('[data-purpose-notice]'))
      notice.textContent=storageOK?'':'再訪時の目的保持が使えません';
  }
  function renderMode() {
    for (const el of document.querySelectorAll('[data-purpose]')) el.value=state.mode;
    for (const panel of document.querySelectorAll('[data-purpose-panel]')) panel.hidden=panel.dataset.purposePanel!==state.mode;
    document.documentElement.dataset.purposeMode=state.mode;
  }
  function setMode(mode) {state.switchMode(mode);persist();renderMode();}
  for (const el of document.querySelectorAll('[data-purpose]')) el.addEventListener('change',()=>setMode(el.value));
  for (const el of document.querySelectorAll('[data-purpose-link]')) el.addEventListener('click',()=>setMode(el.dataset.purposeLink));
  const requested=new URLSearchParams(location.search).get('mode');
  if(root&&MODES.includes(requested)) {
    state.switchMode(requested);
    const url=new URL(location.href);url.searchParams.delete('mode');
    global.history.replaceState(global.history.state,'',url.pathname+url.search+url.hash);
  }
  renderMode();persist();
  global.addEventListener('pageshow',event=>{
    if(!event.persisted)return;
    try {const fresh=JSON.parse(sessionStorage.getItem(KEY)||'{}')||{};state.switchMode(fresh.mode);renderMode();}
    catch(_){storageOK=false;}
  });
  if (!root) return;
  const world=q('[data-purpose-world]'), note=q('[data-purpose-note]'), run=q('[data-purpose-run]');
  world.value=state.world;
  if(world.value!==state.world) {const missing=document.createElement('option');missing.value=state.world;missing.textContent=state.world+'（現在の一覧にはありません）';world.append(missing);world.value=state.world;}
  note.value=state.note;
  function updateInputs() {state.world=world.value;state.note=note.value; if(run)run.href='/jobs'+(state.world?'?world='+encodeURIComponent(state.world):'');persist();}
  world.addEventListener('change',updateInputs);note.addEventListener('input',updateInputs);updateInputs();
  function element(tag,text,parent) {const el=document.createElement(tag);if(text!==undefined) el.textContent=text;if(parent)parent.append(el);return el;}
  function renderCommonRecord(wrapper){
    const r=wrapper.record,p=r.execution_plan;
    const integrity=recordIntegrity(r);
    element('h3',r.run_id+' · 読み込んだ単回RunRecord',result);
    if(r.observations.contract_fixture===true)element('p','契約テスト用のfixtureです。物語の実走行結果ではありません。',result);
    element('p','設定上の目標、実際の終端、観測範囲を別々に表示します。この単回記録から独立試行の結末分布を集計しません。',result);
    element('p','共通API中間版の保存JSONです。計画hashの一致だけで、実seed・provider・保存ログと実状態の整合を保証しません。',result);
    const checks=element('section',undefined,result);checks.setAttribute&&checks.setAttribute('aria-label','記録の整合状態');
    element('h4','記録の整合状態',checks);
    element('p',wrapper.plan_verification==='matched'?'計画本体のhash：この読込で一致を確認しました。':wrapper.plan_verification==='no_plan'?'計画本体のhash：計画の記録なし。':'計画本体のhash：履歴復元時は未照合です。元JSONを読み直すと照合できます。',checks);
    for(const error of integrity.errors){const el=element('p','整合エラー：'+error,checks);el.setAttribute&&el.setAttribute('role','alert');}
    for(const message of integrity.unchecked)element('p','未確認：'+message,checks);
    const meta=element('dl',undefined,result);meta.className='purpose-meta';
    const fields=[['計画の申告種別',p?EXECUTION_KINDS[p.execution_kind]+' / '+p.execution_kind:'計画の記録なし'],
      ['設定上の目標結末',r.configured_target_ending===null?'未設定':Array.isArray(r.configured_target_ending)?r.configured_target_ending.join(', '):r.configured_target_ending],
      ['実際の終端',r.actual_terminal===null?'終端イベントの記録なし':r.actual_terminal.verb],['停止理由',r.stop_reason],
      ['時間軸',r.time_axis.unit+' / '+r.time_axis.start+'〜'+r.time_axis.end],['観測した位置',String(r.observed_through)+' '+r.time_axis.unit],
      ['全run入力hash（RunRecord.run_spec_hash）',r.run_spec_hash],['時間契約hash（time_contract_hashes.run_spec_hash）',r.time_contract_hashes?r.time_contract_hashes.run_spec_hash:'記録なし'],
      ['時間契約の意味hash',r.time_contract_hashes?r.time_contract_hashes.semantic_hash:'記録なし'],
      ['時間契約の表示hash',r.time_contract_hashes?r.time_contract_hashes.presentation_hash:'記録なし'],['エンジンhash',r.engine_hash],
      ['読込記録の識別（run_id＋ledger hash）',wrapper.id],['台帳',r.ledger.path+' / SHA-256 '+r.ledger.sha256],['ExecutionPlan hash',r.execution_plan_hash||'記録なし']];
    if(p){
      fields.push(['計画した試行数',String(p.planned_trials)+'（このJSONは単回結果）'],['事前seed',p.predeclared_seeds.join(', ')],
        ['sampler',canonical(p.sampler)],['provider（計画）',canonical(p.provider)],['選別・育種・原作誘導',canonical(p.selection)],
        ['計画上の観測・停止',canonical(p.observation)],['分類器',canonical(p.classification)]);
      if(p.execution_kind==='explicit_witness')element('p','計画は明示した行動の実行を申告しています。独立試行群としては集計しません。',result);
      if(p.execution_kind==='production_search')element('p','計画は候補探索を申告しています。GA選別群を独立試行分布としては集計しません。',result);
    }
    fields.push(['provider（実行記録）',r.provider_identity===null?'記録なし':canonical(r.provider_identity)],['入力の来歴',canonical(r.input_hashes)]);
    if(r.observed_targets)fields.push(['停止しなかった目標観測',JSON.stringify(r.observed_targets)]);
    for(const [name,value] of fields){element('dt',name,meta);element('dd',value,meta);}
    const terminal=element('details',undefined,result);element('summary','実際の終端の根拠',terminal);
    const t=element('div',r.actual_terminal===null?'終端イベントの記録なし':JSON.stringify(r.actual_terminal,null,2),terminal);t.className='purpose-story';
    const observations=element('details',undefined,result);element('summary','保存された観測値',observations);
    const o=element('div',JSON.stringify(r.observations,null,2),observations);o.className='purpose-story';
    element('p','この画面では計画とseed・providerの対応を照合します。ledger・checkpointの現物は保存ファイルで確認してください。公開画面からの途中再開操作は未接続です。',result);
  }
  function renderResult() {
    copyEpoch++;if(copyStatus)copyStatus.textContent='';if(copyFallback){copyFallback.hidden=true;copyFallback.value='';}
    history.replaceChildren();state.history.forEach((r,i)=>{const opt=element('option',r.world+' · '+r.id,history);opt.value=String(i);});
    history.value=String(state.selected);history.disabled=!state.history.length;result.replaceChildren();
    const r=state.history[state.selected];if(!r)return;
    if(r.kind==='common_run_record'){renderCommonRecord(r);return;}
    if(r.schema==='worldbloom_independent_v2'){
      element('h3',r.world+' ・ 保存済み独立CPU試行',result);
      element('p','母数 '+r.requested+'件、処理済み '+r.completed+'件。'+r.completion+'。未完了・実行失敗も母数に含めます。',result);
      const melos=r.execution_profile==='melos_approved_ninegene_v1';
      if(melos){element('p','承認済みMelos設定です。Melosの通常9遺伝子は固定中立値、ほかの5人物は固定の基礎重みを使います。行為者別ActorViewと観測済み情報で選択します。',result);if(r.provider_assumptions){const assumptions=element('details',undefined,result);element('summary','承認済みの検証用対応表・対象人物',assumptions);element('pre',JSON.stringify(r.provider_assumptions,null,2),assumptions);}const c=melosObservationCounts(r.trials);element('p','保存時点で根拠がある件数：王城への物理到着 '+c.physical_arrival+'件、2分の報告完了 '+c.report_completed+'件、120分の婚礼完了 '+c.wedding_completed+'件。保存記録なし '+c.missing_records+'件。',result);}
      else {element('p','知識の扱い：'+r.knowledge_scope+'。既存互換の候補生成です。人物の未観測情報を完全に遮断できたという保証はありません。',result);
      element('p','Genome：'+(r.genome_kind||'保存時の識別記録なし')+'。DecisionProvider：'+(r.decision_provider_kind||'保存時の識別記録なし')+'。通常9遺伝子の人物比較を検証した結果ではありません。',result);}
      const meta=element('dl',undefined,result);meta.className='purpose-meta';
      for(const k of ['id','world_revision','policy_revision','engine_revision','settings_version','execution_plan_sha256','sampling_rule','classification_rule','source']){element('dt',k,meta);element('dd',r[k],meta);}
      const counts=new Map();for(const t of r.trials)counts.set(t.category,(counts.get(t.category)||0)+1);
      const list=element('ul',undefined,result);for(const [category,count] of counts)element('li',category+'：'+count+'件',list);
      element('p',melos?'物語側の完了、暦の観測終了、残る人物の活動は保存された観測値で別々に確認できます。遺伝子設定を変えた比較試験は含みません。':'分類は記録された終端・停止理由です。原作固有の帰還／挙式分類や本文を生成した結果ではありません。',result);
      for(const t of r.trials){const details=element('details',undefined,result);element('summary','seed '+t.seed+' ・ '+t.status+' / '+t.category,details);element('p','根拠：'+t.evidence_refs.join(', '),details);const raw=element('pre',JSON.stringify(t.run_record||t,null,2),details);raw.className='purpose-story';}
      return;
    }
    const s=summarize(r);
    element('h3',r.world+' · 保存済み独立試行',result);
    element('p','母数 '+s.total+'件。GA選別なし・原作一致による誘導なし。固定モデルの観測件数であり、現実の確率や原作の検証成功を保証しません。',result);
    const meta=element('dl',undefined,result);meta.className='purpose-meta';
    const fields=[['実行方法','independent_trials / unselected / selection:none'],['設定版',r.settings_version],['世界版',r.world_revision],
      ['実行計画',r.execution_plan_id?(r.execution_plan_id+' / SHA-256 '+r.execution_plan_sha256):'旧形式：別のExecutionPlanの記録なし（保存結果の表示のみ）'],
      ['方針版',r.policy_revision],['エンジン版',r.engine_revision],['seed（事前指定）',r.seed_plan.join(', ')],['選択規則',r.sampling_rule],
      ['判定規則',r.classification_rule],['観測終了条件',r.observation_stop],['出典・保存元',r.source]];
    for(const [name,value] of fields){element('dt',name,meta);element('dd',value,meta);}
    const wrap=element('div',undefined,result);wrap.className='purpose-table-wrap';
    const table=element('table',undefined,wrap);table.className='purpose-table';
    element('caption','王城への実到着 × 婚礼の実参加',table);
    const head=element('thead',undefined,table), hr=element('tr',undefined,head);
    for(const label of ['帰還区分','婚礼参加','婚礼不参加','婚礼判定不明','計']){const th=element('th',label,hr);th.scope='col';}
    const body=element('tbody',undefined,table);
    for(const rs of RETURNS){const row=element('tr',undefined,body),th=element('th',LABEL[rs],row);th.scope='row';let total=0;
      for(const ws of WEDDINGS){element('td',String(s.matrix[rs][ws]),row);total+=s.matrix[rs][ws];}element('td',String(total),row);}
    element('p','未確定・実行失敗も母数に含みます。未帰還は観測終了までの不在です。0件は不可能の証明にはなりません。',result);
    element('h4','試行の結末',result);
    const endingList=element('ul',undefined,result);for(const [name,count] of s.outcomes)element('li',name+'：'+count+'件',endingList);
    element('h4','代表的な物語と分岐の根拠',result);
    element('p','各区分の最小seedを代表として表示します。時系列はログの要約です。',result);
    for(const t of s.representatives){const d=element('details',undefined,result);element('summary',LABEL[t.return_status]+' × '+LABEL[t.wedding_status]+' · seed '+t.seed,d);
      element('p','分岐の根拠：'+t.reason,d);element('p','証拠ID：'+t.evidence_ids.join(', '),d);
      const story=element('div',t.story,d);story.className='purpose-story';}
  }
  function busyUI() {file.disabled=state.busy;importButton.disabled=state.busy;cancelButton.disabled=!state.busy;}
  function copyUI(){if(copyButton)copyButton.disabled=!state.history[state.selected];}
  api.acceptSavedResult=async function(raw){
    const token=state.begin();if(token===null)throw new Error('結果の読込中です。');busyUI();
    try {const receipt=await verifyRunRecord(raw);if(token!==state.generation)return false;state.complete(token,receipt);renderResult();copyUI();persist();return true;}
    catch(error){if(token===state.generation)state.cancel();throw error;}
    finally{busyUI();}
  };
  if(copyButton)copyButton.addEventListener('click',async()=>{
    const selected=state.history[state.selected];if(!selected)return;
    const epoch=++copyEpoch;
    const value=JSON.stringify(selected.kind==='common_run_record'?selected.record:selected,null,2)+'\n';
    const label=selected.kind==='common_run_record'?selected.record.run_id:selected.id;
    try{
      if(!global.navigator||!global.navigator.clipboard)throw new Error('clipboard unavailable');
      await global.navigator.clipboard.writeText(value);
      if(epoch!==copyEpoch)return;
      copyStatus.textContent=label+'のJSONをコピーしました。';copyFallback.hidden=true;
    }catch(_){
      if(epoch!==copyEpoch)return;
      copyStatus.textContent=label+'のJSONを選択してコピーしてください。';copyFallback.value=value;copyFallback.hidden=false;
      if(copyFallback.focus)copyFallback.focus();if(copyFallback.select)copyFallback.select();
    }
  });
  importButton.addEventListener('click',()=>{
    if(state.busy)return;
    const chosen=file.files&&file.files[0];
    if(!chosen){status.textContent='結果JSONを選んでください。';return;}
    if(chosen.size>MAX_BYTES){status.textContent='結果は最大16MiBです。過去結果は保持しています。';return;}
    const token=state.begin();if(token===null)return;busyUI();status.textContent='保存済み結果を読み込んでいます…';
    const current=new FileReader();reader=current;
    current.onload=async()=>{
      if(token!==state.generation)return;
      try {
        let receipt=validateReceipt(JSON.parse(current.result));
        if(receipt.kind==='common_run_record'||receipt.schema==='worldbloom_independent_v2')receipt=await verifyRunRecord(receipt);
        if(token!==state.generation)return;
        if(state.complete(token,receipt)){renderResult();copyUI();persist();status.textContent='保存済み結果を読み込みました。新規実行は行っていません。';}
      }
      catch(e){if(token!==state.generation)return;state.cancel();status.textContent=e.message+' 過去結果は保持しています。';}
      reader=null;busyUI();
    };
    current.onerror=()=>{if(token!==state.generation)return;state.cancel();reader=null;busyUI();status.textContent='ファイルを読めませんでした。過去結果は保持しています。';};
    try {current.readAsText(chosen,'UTF-8');}
    catch (_) {state.cancel();reader=null;busyUI();status.textContent='ファイルを読めませんでした。過去結果は保持しています。';}
  });
  function cancelRead() {state.cancel();if(reader){reader.abort();reader=null;}busyUI();}
  cancelButton.addEventListener('click',()=>{cancelRead();status.textContent='読込を取り消しました。過去結果は保持しています。';});
  global.addEventListener('pagehide',()=>{cancelRead();persist();});
  global.addEventListener('pageshow',event=>{
    if(event.persisted){
      try {
        const fresh=new State(JSON.parse(sessionStorage.getItem(KEY)||'{}'));
        state.switchMode(fresh.mode);
        for(const receipt of fresh.history) if(!state.history.some(r=>r.id===receipt.id))state.history.push(receipt);
        renderMode();renderResult();copyUI();
      }catch(_){storageOK=false;}
    }
    busyUI();if(status.textContent==='保存済み結果を読み込んでいます…')status.textContent='画面を離れたため読込を取り消しました。';
  });
  history.addEventListener('change',()=>{const i=Number(history.value);if(Number.isInteger(i)&&state.history[i]){state.selected=i;renderResult();copyUI();persist();}});
  renderResult();busyUI();copyUI();
})(typeof window!=='undefined'?window:globalThis);
