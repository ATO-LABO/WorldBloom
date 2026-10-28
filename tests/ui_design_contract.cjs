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
assert(shared.includes('.ui-edit-dialog,.wp-dialog'), 'Draft and saved-world edit dialogs must share their appearance');
assert(!/\.wp-dialog\b/.test(fs.readFileSync(path.join(dir,'world-prototype.css'),'utf8')), 'Dialog appearance belongs to shared UI, not the world page');
for (const name of ['world_create.py','global_settings.py']) {
  const source = fs.readFileSync(path.join(root,'viewer',name),'utf8');
  assert(!source.includes("replace('</head>'"), `${name}: use renderer assets, not post-load overrides`);
}
assert.deepEqual(failures, [], failures.join('\n'));
console.log('Shared UI design contract passed');
