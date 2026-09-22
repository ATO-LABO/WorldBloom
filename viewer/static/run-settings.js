"use strict";
(() => {
  const form=document.querySelector("[data-run-settings]");
  if(!form)return;
  const worlds=JSON.parse(form.dataset.worlds);
  const field=name=>form.elements.namedItem(name);
  const $=selector=>form.querySelector(selector);
  const mode=$("[data-ending-mode]"), ending=field("evolution.target_ending");
  let customEnding=ending.value;
  function worldSummary(){
    const id=field("project_id").value, w=worlds[id];
    const custom=mode.value==="custom";
    ending.closest(".field").hidden=!custom;
    const selected=ending.value.split(",").map(s=>s.trim()).filter(Boolean);
    $("[data-ending-description]").textContent=w ? (custom ? selected.map(k=>w.labels[k]||k).join("、") || "有効な結末IDを入力してください。" : w.ending) : "世界を選ぶと結末を表示します。";
    $("[data-world-facts]").textContent=w ? `主人公 ${w.protagonist}  ·  敵役 ${w.antagonist}` : "";
    for(const link of form.querySelectorAll("[data-world-link],[data-world-back]")){
      link.href=id?`/worlds/${encodeURIComponent(id)}`:"/";
      if(link.hasAttribute("data-world-link"))link.hidden=!id;
    }
  }
  function summary(){
    const num=name=>{const value=Number(field(name).value);return Number.isSafeInteger(value)&&value>0?value:null;};
    const g=num("evolution.generations"),p=num("evolution.population"),s=num("evolution.seeds");
    const factor=field("evolution.coevolve").checked?2:1;
    const format=n=>Number.isSafeInteger(n)&&n>0?n.toLocaleString("ja-JP"):"—";
    $("[data-scale-equation]").textContent=`${g??"—"}世代 × ${p??"—"}個体 × ${s??"—"}回${factor===2?" × 2陣営":""}`;
    $("[data-individual-total]").textContent=format(g&&p?g*p*factor:null);
    $("[data-total]").textContent=format(g&&p&&s?g*p*s*factor:null);
    $("[data-per-gen]").textContent=format(p&&s?p*s*factor:null);
    const seconds=num("execution_limits.wall_seconds");
    $("[data-limit-summary]").textContent=`乱数・並列数・時間上限 ${seconds?Number((seconds/60).toFixed(1))+"分":"未設定"}`;
    worldSummary();
  }
  mode.addEventListener("change",()=>{
    if(mode.value==="default"){customEnding=ending.value;ending.value="";}
    else ending.value=customEnding;
    summary();
    if(mode.value==="custom")ending.focus();
  });
  form.addEventListener("input",summary);
  form.addEventListener("change",summary);
  $("[data-saved-config]").addEventListener("change",event=>{
    if(event.target.value)location.href=`/configs/new?from=${encodeURIComponent(event.target.value)}`;
  });
  for(const input of form.querySelectorAll(".rs-scale input[type=number]")){
    if(input.closest("details"))continue;
    const group=document.createElement("div");group.className="rs-stepper";
    input.before(group);group.append(input);
    const label=form.querySelector(`label[for="${input.id}"]`)?.childNodes[0]?.textContent?.trim() || "値";
    for(const [symbol,delta,verb] of [["−",-1,"減らす"],["＋",1,"増やす"]]){
      const button=document.createElement("button");button.type="button";button.textContent=symbol;button.setAttribute("aria-label",`${label}を${verb}`);
      if(delta<0)group.prepend(button);else group.append(button);
      button.addEventListener("click",()=>{
        const current=Number(input.value),minimum=Number(input.min||1);
        input.value=String(Math.max(minimum,(Number.isFinite(current)?current:minimum)+delta));
        input.dispatchEvent(new Event("input",{bubbles:true}));
      });
    }
  }
  const reveal=element=>{const details=element.closest("details");if(details)details.open=true;};
  form.addEventListener("invalid",e=>reveal(e.target),true);
  const errors=new MutationObserver(()=>{
    const targets=[...form.querySelectorAll("[data-error-for]")].filter(e=>e.textContent.trim());
    targets.forEach(reveal);
    if($("[data-form-error]").textContent.trim() || targets.length){
      $(".rs-editor").scrollTop=0;
      const input=targets[0] && field(targets[0].dataset.errorFor);
      (input || $("[data-form-error]")).focus();
    }
  });
  for(const error of form.querySelectorAll("[data-error-for],[data-form-error]")) errors.observe(error,{subtree:true,childList:true,characterData:true});
  summary();
})();
