// Enforced by the existing JavaScript test discovery, without extra dependencies.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..'), dir = path.join(root, 'viewer/static');
const legacy = new Set(['app.css','comparison.css','generation.css',
  'genre-workspace.css','home-workspace.css','lineage-workspace.css',
  'raw-workspace.css','review-workspace.css','run-settings.css','run-workspace.css',
  'screening-workspace.css','sifting-workspace.css','time-settings.css','world-prototype.css']);
const failures = [];
for (const name of fs.readdirSync(dir).filter(n => n.endsWith('.css'))) {
  const source = fs.readFileSync(path.join(dir,name),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
  if (name !== 'ui-tokens.css' && /--ws-[\w-]+\s*:/.test(source)) failures.push(`${name}: shared token redefinition`);
  if (!['ui.css','ui-tokens.css'].includes(name) && /\.purpose-(?:context|picker|workspace|top-status)\b/.test(source)) failures.push(`${name}: purpose header styles belong to ui.css`);
  if (name !== 'ui-tokens.css') {
    for (const rule of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const [,selector,body] = rule;
      if (/\bsvg\b|plot|river-svg|\.ga-replay|\.river-node/.test(selector)) continue;
      for (const size of body.matchAll(/(?:^|;)\s*font-size\s*:\s*([\d.]+)(px|rem|em)\b/g)) {
        failures.push(`${name}: ${selector.trim()}: UI font size must use shared tokens (${size[0].trim()})`);
      }
    }
  }
  if (legacy.has(name) || ['ui.css','ui-tokens.css'].includes(name)) continue;
  for (const match of source.matchAll(/(?:^|[;{])\s*([\w-]+)\s*:\s*([^;}]+)/g)) {
    const [,prop,value] = match;
    if (!/^(font(?:-family|-size)?|line-height|letter-spacing|border-radius|(?:padding|margin)(?:-[\w-]+)?|(?:row-|column-)?gap|color|background(?:-color)?)$/.test(prop)) continue;
    const remainder = value.replace(/var\([^)]*\)/g,'').replace(/\b(?:inherit|normal|auto|none|transparent|currentColor)\b/g,'').replace(/\b0\b/g,'').trim();
    if (remainder) failures.push(`${name}: ${prop}: ${value.trim()} must use shared tokens`);
  }
}
const documentSource = fs.readFileSync(path.join(root,'viewer/pages.py'),'utf8');
assert(documentSource.includes('/static/ui-tokens.css') && documentSource.includes('/static/ui.css'), 'Default renderer must include shared design');
const shared=fs.readFileSync(path.join(dir,'ui.css'),'utf8');
assert(shared.includes(':not([aria-current="false"])'), 'Inactive sidebar items must not receive the current-item style');
assert(!/\.wp-nav/.test(fs.readFileSync(path.join(dir,'world-prototype.css'),'utf8')), 'World sidebar controls belong to the shared component');
const rulesSource=fs.readFileSync(path.join(root,'viewer/world_advanced.py'),'utf8');
assert(rulesSource.includes('class="ui-nav"')&&rulesSource.includes("page_class='settings-workspace'"), 'World rules must use the shared sidebar and settings shell');
assert(shared.includes('.ui-edit-dialog,.wp-dialog'), 'Draft and saved-world edit dialogs must share their appearance');
assert(!/\.wp-dialog\b/.test(fs.readFileSync(path.join(dir,'world-prototype.css'),'utf8')), 'Dialog appearance belongs to shared UI, not the world page');
for (const name of ['world_create.py','global_settings.py']) {
  const source = fs.readFileSync(path.join(root,'viewer',name),'utf8');
  assert(!source.includes("replace('</head>'"), `${name}: use renderer assets, not post-load overrides`);
}
assert.deepEqual(failures, [], failures.join('\n'));
console.log('Shared UI design contract passed');

// Static cascade/DOM contract, not a browser or pixel-rendering test.
{
  const vm = require('node:vm');
  const script = fs.readFileSync(path.join(dir,'world-prototype.js'),'utf8');
  const renderer = fs.readFileSync(path.join(root,'viewer/world_prototype.py'),'utf8');
  const css = fs.readFileSync(path.join(dir,'world-prototype.css'),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([,selector,body])=>({
    selector:selector.trim(), declarations:Object.fromEntries(body.split(';').filter(x=>x.includes(':')).map(x=>{
      const i=x.indexOf(':');return [x.slice(0,i).trim(),x.slice(i+1).trim()];
    }))
  }));
  const resets=rules.filter(r=>r.declarations.background==='transparent'&&r.declarations.border==='0'&&r.declarations['box-shadow']==='none');
  assert.equal(resets.length,1,'Keep a single ordinary world-button reset');
  const reset=resets[0];
  // Evaluate the reset's descendant/tag/class selectors and zero-specificity
  // :where(:not(.class)) filter against the actual confirmation controls.
  const matches=(el,selector)=>{
    const excluded=[...selector.matchAll(/:where\(:not\((\.[\w-]+)\)\)/g)].map(m=>m[1].slice(1));
    if(excluded.some(name=>el.className.split(/\s+/).includes(name)))return false;
    const parts=selector.replace(/:where\(:not\(\.[\w-]+\)\)/g,'').trim().split(/\s+/);
    const simple=(node,s)=>s.startsWith('.')?node.className.split(/\s+/).includes(s.slice(1)):node.tagName===s;
    if(!simple(el,parts.pop()))return false;
    let ancestor=el.parent;
    for(const s of parts.reverse()){
      while(ancestor&&!simple(ancestor,s))ancestor=ancestor.parent;
      if(!ancestor)return false;
      ancestor=ancestor.parent;
    }
    return true;
  };
  class Element {
    constructor(tag){this.tagName=tag;this.className='';this.children=[];this.listeners={};this.textContent='';}
    append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
    replaceChildren(){this.children=[];this.textContent='';}
    addEventListener(name,fn){this.listeners[name]=fn;}
    focus(){this.focused=true;}
    click(){this.listeners.click();}
  }
  // Build ancestry from the real renderer template, rather than assuming that
  // a top-layer dialog is detached from the .wp element in the DOM.
  const markup=renderer.slice(renderer.indexOf("body = f'''"),renderer.indexOf('doc = pages.document'));
  const stack=[],ids={};let worldRoot,dialog;
  for(const [,closing,tag,attrs] of markup.matchAll(/<(\/?)([a-z][\w-]*)\b([^>]*)>/g)){
    if(closing){while(stack.length&&stack.pop().tagName!==tag){};continue;}
    const el=new Element(tag);el.className=attrs.match(/class="([^"]*)"/)?.[1]||'';
    const id=attrs.match(/id="([^"]*)"/)?.[1];if(id)ids[id]=el;
    if(stack.length)stack.at(-1).append(el);
    if(el.className.split(/\s+/).includes('wp'))worldRoot=el;
    if(tag==='dialog')dialog=el;
    if(!['input','br','hr','meta','link','img','source','wbr'].includes(tag))stack.push(el);
  }
  assert(worldRoot&&dialog&&ids['wp-fields']);
  assert(script.includes("<p id=\"wp-edit-error\" role=\"alert\"></p>"),'Use the error host created by the real editor');
  const start=script.indexOf('function closeEditor(){');
  const end=script.indexOf("form.addEventListener('input'",start);
  assert(start>=0&&end>start,'Exercise the actual closeEditor implementation');
  const closeSource=script.slice(start,end);
  const box=new Element('p');ids['wp-fields'].append(box);ids['wp-edit-error']=box;
  let closes=0,unexpected=0;dialog.close=()=>{closes++;};
  const input=new Element('input');
  const draft=[{condition:'safe',priority:3}];
  const context={busy:false,changed:true,commitmentDraft:draft,dialog,
    document:{getElementById:id=>ids[id],createElement:tag=>new Element(tag),
      createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
      querySelector:selector=>{assert.equal(selector,'[data-person-commitments-editor] input');return input;}},
    window:{confirm(){unexpected++;throw Error('Values editor must use its inline confirmation');}},
    fetch(){unexpected++;throw Error('Discard/resume must not save');}};
  vm.createContext(context);vm.runInContext(closeSource,context);context.closeEditor();
  const buttons=box.children.filter(el=>el.tagName==='button');
  assert.deepEqual(buttons.map(el=>el.textContent),['変更を破棄する','編集を続ける']);
  for(const button of buttons){
    assert(button.className.split(/\s+/).includes('ui-button'));
    assert(!matches(button,reset.selector),'Values-editor confirmation must retain shared border/background instead of matching the legacy reset');
  }
  assert(buttons[1].focused);assert.equal(closes,0);
  const ordinary=new Element('button');worldRoot.append(ordinary);
  assert(matches(ordinary,reset.selector),'Ordinary world buttons still use the reset');
  assert.deepEqual(reset.declarations,{cursor:'pointer',color:'inherit',background:'transparent',border:'0','border-radius':'0','box-shadow':'none','line-height':'var(--ws-line-height)',padding:'var(--ws-space-2) var(--ws-space-3)'});
  assert(shared.includes('border:1px solid var(--ws-control-line)')&&shared.includes('background:var(--ws-surface)'),'Shared stylesheet owns the confirmation border/background');
  buttons[1].click();assert.equal(box.children.length,0);assert(input.focused);
  assert.equal(context.changed,true);assert.equal(closes,0);assert.equal(context.commitmentDraft,draft);
  context.closeEditor();box.children.find(el=>el.textContent==='変更を破棄する').click();
  assert.equal(context.changed,false);assert.equal(closes,1);assert.equal(unexpected,0);
  assert.deepEqual(draft,[{condition:'safe',priority:3}]);
  console.log('Values-editor confirmation contract passed: actual discard/resume controls, legacy reset exclusion, unchanged ordinary reset, focus/input retention, and no save. Static CSS/DOM only.');
}
