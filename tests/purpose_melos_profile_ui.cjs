const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {environment}=require('./purpose_dom_ui.cjs');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const e=environment(),C=e.fields.status.constructor,fields={};for(const n of ['config','seeds','start','retry','cancel','status','plan','melos-profile'])fields[n]=new C();
 e.context.Event=class {constructor(type){this.type=type;}};
 e.fields.world.dispatchEvent=event=>e.fields.world.fire(event.type);
 Object.defineProperty(fields.config,'options',{get(){return this.children;}});fields.seeds.value='0';
 const root=new C();root.querySelector=s=>fields[s.match(/data-independent-(.*)\]/)[1]];
 e.context.document.querySelector=s=>s==='[data-independent-root]'?root:s==='[data-purpose-world]'?e.fields.world:null;
 e.context.crypto=crypto.webcrypto;e.context.setTimeout=()=>1;e.context.clearTimeout=()=>{};
 const H='a'.repeat(64),cap={schema_version:1,supported:true,config_id:'cfg-melos',world:'melos-input',method:'independent_trials',population:'unselected',selection:'none',reference_guided:false,max_trials:100,execution_profile:{id:'melos_approved_ninegene_v1',version:1},...Object.fromEntries(['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'].map(k=>[k,H]))};
 let saved=false,saveCount=0,jobCount=0,resolveSave;
 const response=v=>({ok:true,status:200,json:async()=>v});
 e.context.fetch=async(url,options)=>{
  if(url==='/api/independent/capabilities')return response({configurations:saved?[cap]:[]});
  if(url.includes('capabilities?'))return response(cap);
  if(url==='/api/configs'){saveCount++;assert.equal(options.headers['X-WorldBloom-Client'],'1');const spec=JSON.parse(options.body);assert.equal(spec.execution_profile.id,'melos_approved_ninegene_v1');assert.equal(spec.execution_profile.genome.category_weight.I,.5);return new Promise(resolve=>{resolveSave=()=>{saved=true;resolve(response({config_id:cap.config_id}));};});}
  if(url==='/api/independent/jobs'){jobCount++;throw new Error('Profile selection must not start a trial');}
  throw new Error('Unexpected '+url);
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../viewer/static/purpose-independent.js'),'utf8'),e.context);await tick();
 fields['melos-profile'].fire('click');fields['melos-profile'].fire('click');await tick();assert.equal(saveCount,1);assert.equal(fields['melos-profile'].disabled,true);resolveSave();await tick();await tick();
 assert.equal(fields.config.value,'cfg-melos');assert.equal(e.fields.world.value,'melos-input');assert.equal(jobCount,0);assert.equal(fields['melos-profile'].disabled,false);
 assert.equal(e.stored().world,'melos-input');
 assert.equal(e.fields.run.href,'/jobs?world=melos-input');
 const restored=environment({initialSaved:e.stored()});assert.equal(restored.fields.world.value,'melos-input');
 fields['melos-profile'].fire('click');await tick();await tick();assert.equal(saveCount,1);assert.equal(jobCount,0);
 console.log('Melos explicit profile DOM checks passed: neutral spec, CSRF, burst guard, existing configuration reuse, no automatic trial execution. Mock transport only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
