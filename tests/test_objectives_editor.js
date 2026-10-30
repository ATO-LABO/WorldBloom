const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{}};vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../viewer/static/objectives-editor.js'),'utf8'),context);
const editor=context.window.WorldBloomObjectives,plain=v=>JSON.parse(JSON.stringify(v));
test('changing ALL to ANY retains every authored branch and keeps the source draft independent',()=>{
 const failure={all:[{any:[{condition:'blocked'},{condition:'ended'}]},{not:{condition:'food_taken'}}]};
 const saved=plain(failure),changed=plain(editor.convert(failure,'any','fallback'));
 assert.deepEqual(changed,{any:saved.all});changed.any[0].any[0].condition='new';assert.deepEqual(failure,saved);
});
test('wrapping an existing predicate preserves its identity, including literal markup',()=>{
 const original={condition:'<img onerror=alert(1)>'};
 assert.deepEqual(plain(editor.convert(original,'not','other')),{not:original});
 assert.deepEqual(plain(editor.convert(original,'all','other')),{all:[original]});
 assert.deepEqual(plain(editor.convert({not:original},'condition','other')),original);
});
