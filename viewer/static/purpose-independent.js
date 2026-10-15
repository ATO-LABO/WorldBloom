/* Durable local independent jobs. Mode changes affect presentation only. */
(function(global){
  'use strict';
  const KEY='worldbloom-independent-job-v1', TERMINAL=['succeeded','failed','cancelled','interrupted'];
  const clone=v=>JSON.parse(JSON.stringify(v));
  function melosProfileSpec(){
    return {label:'Melos 通常9遺伝子・固定中立設定',project_id:'melos-input',template_id:'momotaro',
      evolution:{population:1,generations:1,seeds:1,processes:1,kappa:0,route_rho:0},
      execution_profile:{id:'melos_approved_ninegene_v1',version:1,
        genome:{category_weight:{I:0.5,II:0.5,III:0.5,IV:0.5,V:0.5,VI:0.5},novelty_drive:0.0,risk_tolerance:0.5,stance_shift_bias:0.0}}};
  }
  function seeds(text){
    const parts=text.trim().split(/[\s,]+/);
    if(!text.trim()||parts.length>100||parts.some(v=>!/^\d+$/.test(v)))throw new Error('seedは0以上の整数を1〜100件指定してください。');
    const values=parts.map(Number);
    if(values.some(v=>!Number.isSafeInteger(v)||v>2147483647)||new Set(values).size!==values.length)throw new Error('seedの範囲と重複を確認してください。');
    return values;
  }
  function validateCapability(v){
    if(!v||v.schema_version!==1||typeof v.supported!=='boolean'||typeof v.config_id!=='string'||v.method!=='independent_trials'||v.population!=='unselected'||v.selection!=='none'||v.reference_guided!==false||v.max_trials!==100)throw new Error('独立試行の利用条件が不正です。');
    for(const k of ['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'])if(!/^[a-f0-9]{64}$/.test(v[k]))throw new Error('入力の版を確認できません。');
    return clone(v);
  }
  function makeRequest(cap,text,id){
    cap=validateCapability(cap);if(!cap.supported)throw new Error(cap.reason||'この設定では独立CPU試行を利用できません。');
    if(typeof id!=='string'||!/^review-[a-zA-Z0-9-]+$/.test(id))throw new Error('要求IDが不正です。');
    return {request_id:id,kind:'independent_trials',config_id:cap.config_id,...Object.fromEntries(['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'].map(k=>[k,cap[k]])),seed_plan:seeds(text),method:'independent_trials',population:'unselected',selection:'none',reference_guided:false};
  }
  class Session {
    constructor(saved={},save=()=>{}){
      this.active=null;this.operation=0;this.pending=false;this.save=save;
      if(saved.active&&typeof saved.active==='object'){
        const a=saved.active,r=a.request;
        if(r&&r.kind==='independent_trials'&&r.method==='independent_trials'&&r.selection==='none'&&r.reference_guided===false&&typeof r.request_id==='string')this.active=clone(a);
      }
    }
    persist(){this.save({active:this.active?clone(this.active):null});}
    suspend(){this.operation++;this.pending=false;this.persist();}
    async start(request,send){
      if(this.pending)return null;
      if(this.active&&!this.active.resultLoaded&&(!this.active.job||!TERMINAL.includes(this.active.job.state)||!this.active.job.termination_confirmed))throw new Error('先の要求を再確認してください。');
      this.active={request:clone(request),job:null,resultLoaded:false};this.persist();return this.retry(send);
    }
    async retry(send){
      if(this.pending||!this.active)return null;
      const op=++this.operation,request=clone(this.active.request);this.pending=true;
      try{
        const job=await send(request);
        if(op!==this.operation)return null;
        if(job.request_id!==request.request_id||!job.accepted_request||JSON.stringify(job.accepted_request)!==JSON.stringify(request)){
          // Compare canonical key order too: JSON object order has no meaning.
          if(job.request_id!==request.request_id||JSON.stringify(sort(job.accepted_request))!==JSON.stringify(sort(request)))throw new Error('確定要求とジョブが一致しません。');
        }
        this.active.job=clone(job);this.persist();return job;
      }finally{if(op===this.operation)this.pending=false;}
    }
    acceptJob(job){
      if(!this.active||!this.active.job||job.job_id!==this.active.job.job_id||job.request_id!==this.active.request.request_id||job.execution_plan_sha256!==this.active.job.execution_plan_sha256)throw new Error('別ジョブの応答は表示できません。');
      if(job.revision<this.active.job.revision)return this.active.job;
      this.active.job=clone(job);this.persist();return job;
    }
    async cancel(send){
      if(!this.active||!this.active.job||this.pending)return null;
      const op=++this.operation;this.pending=true;const id=this.active.job.job_id;
      try{const job=await send(id);if(op!==this.operation)return null;return this.acceptJob(job);}
      finally{if(op===this.operation)this.pending=false;}
    }
  }
  function sort(v){if(Array.isArray(v))return v.map(sort);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sort(v[k])]));return v;}
  const api={melosProfileSpec,seeds,validateCapability,makeRequest,Session,TERMINAL};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  global.WorldBloomIndependent=api;
  if(typeof document==='undefined')return;
  const root=document.querySelector('[data-independent-root]');if(!root)return;
  const q=s=>root.querySelector(s),config=q('[data-independent-config]'),seed=q('[data-independent-seeds]'),start=q('[data-independent-start]'),retry=q('[data-independent-retry]'),cancel=q('[data-independent-cancel]'),status=q('[data-independent-status]'),plan=q('[data-independent-plan]');
  const profile=q('[data-independent-melos-profile]');let profilePending=false;
  const world=document.querySelector('[data-purpose-world]');let saved={},caps=[],pollTimer=null,epoch=0,leaving=false,loadingResult=false;
  try{saved=JSON.parse(sessionStorage.getItem(KEY)||'{}');}catch(_){}
  const session=new Session(saved,value=>{try{sessionStorage.setItem(KEY,JSON.stringify(value));}catch(_){status.textContent='この画面内で保持しています。再訪時に要求を復元できないため、ジョブIDを控えてください。';}});
  async function request(url,body){
    const response=await fetch(url,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:body===undefined?undefined:JSON.stringify(body)});
    const value=await response.json();if(!response.ok){const error=new Error(value.message||value.error||'保存状態を確認できません。');error.status=response.status;throw error;}return value;
  }
  const path=id=>'/api/independent/jobs/'+encodeURIComponent(id);
  function render(){
    const a=session.active,j=a&&a.job,terminal=j&&TERMINAL.includes(j.state)&&j.termination_confirmed;
    start.disabled=session.pending||!!(a&&(!j||!terminal));retry.disabled=session.pending||!a;
    cancel.disabled=session.pending||!j||terminal;
    if(j){const p=j.progress||{};status.textContent='ジョブ '+j.job_id+'：'+j.state+' / '+j.phase+'。処理済み '+(p.completed||0)+' / '+(p.requested||a.request.seed_plan.length)+'件。'+(j.cancel_requested_at?(j.termination_confirmed?'取消要求済み。所有プロセスの終了を確認しました。':'取消要求済み。所有プロセスの終了確認を待っています。'):'');}
    plan.textContent=a?JSON.stringify({request:a.request,execution_plan_sha256:j&&j.execution_plan_sha256,termination_confirmed:j&&j.termination_confirmed},null,2):'設定を選ぶと、実行前にseedと入力の版を固定します。';
  }
  async function poll(){
    if(leaving||!session.active||!session.active.job)return;
    const token=epoch,id=session.active.job.job_id;
    try{
      const job=await request(path(id));if(token!==epoch||leaving)return;session.acceptJob(job);render();
      if(TERMINAL.includes(job.state)&&job.termination_confirmed){
        if(!session.active.resultLoaded&&!loadingResult){
          loadingResult=true;
          try{const result=await request(path(id)+'/result');if(token!==epoch||leaving)return;
            if(result.id!==id||result.execution_plan_sha256!==job.execution_plan_sha256)throw new Error('取得結果と確定計画が一致しません。');
            const accepted=await global.WorldBloomPurpose.acceptSavedResult(result);if(token!==epoch||leaving)return;
            if(accepted){session.active.resultLoaded=true;session.persist();status.textContent+=' 保存結果を履歴へ読み込みました。';}
          }catch(error){if(token===epoch&&!leaving)status.textContent+=' '+(error.status===404?'保存結果はまだありません。':error.message);}
          finally{loadingResult=false;}
        }return;
      }
    }catch(error){if(token===epoch&&!leaving)status.textContent=error.message+' 同じジョブを再確認できます。';}
    if(token===epoch&&!leaving)pollTimer=setTimeout(poll,1500);
  }
  function schedule(){clearTimeout(pollTimer);epoch++;leaving=false;render();poll();}
  function configurations(){
    const previous=config.value;config.replaceChildren();
    for(const c of caps.filter(c=>!world||!world.value||c.world===world.value)){const option=document.createElement('option');option.value=c.config_id;option.textContent=c.world+' / '+c.config_id+(c.supported?'':'（利用不可）');config.append(option);}
    if([...config.options].some(o=>o.value===previous))config.value=previous;
    start.disabled=start.disabled||!config.value;
  }
  if(profile)profile.addEventListener('click',async()=>{
    if(profilePending)return;profilePending=true;profile.disabled=true;
    try{
      const existing=await request('/api/independent/capabilities');caps=existing.configurations.map(validateCapability);
      let selected=caps.find(c=>c.world==='melos-input'&&c.execution_profile&&c.execution_profile.id==='melos_approved_ninegene_v1'&&c.execution_profile.version===1);
      if(!selected){const created=await request('/api/configs',melosProfileSpec());selected=validateCapability(await request('/api/independent/capabilities?config_id='+encodeURIComponent(created.config_id)));caps.push(selected);}
      if(leaving)return;
      if(world){world.value=selected.world;world.dispatchEvent(new Event('change',{bubbles:true}));}
      render();configurations();config.value=selected.config_id;
      status.textContent='Melosの固定中立設定を選択しました。事前seedを確認して開始してください。'+(selected.supported?'':(' '+selected.reason));
    }catch(error){status.textContent=error.message+' 保存済み設定を再確認してから選択します。';}
    finally{profilePending=false;profile.disabled=false;}
  });
  start.addEventListener('click',async()=>{
    if(session.pending)return;
    epoch++;clearTimeout(pollTimer);
    try{const cap=validateCapability(await request('/api/independent/capabilities?config_id='+encodeURIComponent(config.value)));
      if(session.pending||leaving)return;
      const id='review-'+global.crypto.randomUUID(),r=makeRequest(cap,seed.value,id);
      const pending=session.start(r,body=>request('/api/independent/jobs',body));render();await pending;schedule();
    }catch(error){render();status.textContent=error.message+' 同じ要求の再確認で重複実行を避けます。';}
  });
  retry.addEventListener('click',async()=>{try{if(session.active&&session.active.job){schedule();return;}const pending=session.retry(body=>request('/api/independent/jobs',body));render();await pending;schedule();}catch(error){render();status.textContent=error.message;}});
  cancel.addEventListener('click',async()=>{try{const pending=session.cancel(id=>request(path(id)+'/cancel',{}));render();await pending;schedule();}catch(error){render();status.textContent=error.message;}});
  if(world)world.addEventListener('change',configurations);
  global.addEventListener('pagehide',()=>{leaving=true;epoch++;clearTimeout(pollTimer);session.suspend();});
  global.addEventListener('pageshow',event=>{if(!event.persisted)return;try{const fresh=JSON.parse(sessionStorage.getItem(KEY)||'{}');session.active=new Session(fresh).active;}catch(_){}schedule();});
  render();request('/api/independent/capabilities').then(value=>{caps=value.configurations.map(validateCapability);render();configurations();}).catch(error=>{start.disabled=true;status.textContent=error.message;});
  if(session.active&&session.active.job)schedule();
})(typeof window!=='undefined'?window:globalThis);
