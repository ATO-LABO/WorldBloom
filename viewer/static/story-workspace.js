"use strict";
(() => {
 const host=document.querySelector("[data-story]");if(!host)return;
 const cfg=JSON.parse(host.dataset.story);
 const message=host.querySelector("[data-story-message]");
 const value=sel=>host.querySelector(sel)?.value;
 const score=name=>{const n=value("[data-story-score="+name+"]");return n?Number(n):null;};
 let busy=false,dirty=false;
 host.addEventListener("input",event=>{
  if(event.target.matches("[data-story-focus],[data-story-viewpoint],[data-story-tone],[data-story-edit-json]")) {
   dirty=true;const confirm=host.querySelector("[data-story-confirm]");if(confirm)confirm.disabled=true;
   message.textContent="変更を保存してから、この骨格を確認してください。";
  }
 });
 async function post(url,body) {
  const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-WorldBloom-Client":"1"},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok)throw new Error(data.message || "保存できません");
  return data;
 }
 const base="/api/stories/runs/"+encodeURIComponent(cfg.run_id)+"/"+encodeURIComponent(cfg.candidate_id);
 const out="/api/stories/outputs/"+encodeURIComponent(cfg.output_id)+"/"+encodeURIComponent(cfg.candidate_id);
 host.addEventListener("click",async event=>{
  const target=event.target.closest("button");if(!target||busy)return;
  if(!Object.keys(target.dataset).some(k=>k.startsWith("story")))return;
  busy=true;target.disabled=true;message.textContent="処理中です…";
  try {
   if(target.hasAttribute("data-story-organize")) {
    const id=cfg.config_id || value("[data-story-config]");if(!id)throw new Error("設定を選択してください");
    await post(base+"/organize",{config_id:id});
   } else if(target.hasAttribute("data-story-save")) {
    const advanced=JSON.parse(value("[data-story-edit-json]"));
    await post(base+"/edit",{expected_revision:cfg.revision,changes:{...advanced,
     focus:value("[data-story-focus]"),viewpoint:value("[data-story-viewpoint]"),tone:value("[data-story-tone]"),
     narrative_threads:advanced.narrative_threads}});
   } else if(target.hasAttribute("data-story-confirm")) {
    if(dirty)throw new Error("変更を先に保存してください");
    await post(base+"/confirm",{plan_sha256:cfg.plan_sha256});
   } else if(target.hasAttribute("data-story-recheck")) {
    await post(out+"/recheck",{});
   } else if(target.hasAttribute("data-story-review")) {
    await post(out+"/review",{text_sha256:cfg.text_sha256,adoption:value("[data-story-adoption]"),note:value("[data-story-review-note]"),causal_clarity:score("causal_clarity"),story_flow:score("story_flow"),surprise:score("surprise")});
    message.textContent="この本文の版に評価を保存しました。";location.reload();return;
   } else return;
   location.reload();
  } catch(error) {message.textContent=error.message+"。画面の入力を保全しました。版の競合なら別タブで現行版を確認してください。";}
  finally {busy=false;target.disabled=false;}
 });
})();