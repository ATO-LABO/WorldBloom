const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.dataset={};this.events={};this.children=[];this.textContent='';this.value='';}
 addEventListener(key,fn){this.events[key]=fn;}
 append(...items){this.children.push(...items);}
 setAttribute(k,v){this[k]=v;}
 focus(){this.focused=true;}
 select(){this.selected=true;}
 showModal(){this.open=true;}
 close(){this.open=false;this.events.close?.();}
 remove(){this.removed=true;}
}
(async()=>{
 const original=new Element('code'),status=new Element(),root=new Element(),source=new Element('script');
 original.textContent=' { "n":9007199254740993, "text":"<tag> & 花束\\n" } ';
 source.textContent=JSON.stringify(original.textContent+'\r\u0000');
 const exactText=JSON.parse(source.textContent);
 root.dataset={line:'7',source:'a'.repeat(64)};
 const record=new Element('button'),link=new Element('button');
 record.dataset.rvCopy='record';link.dataset.rvCopy='link';
 root.querySelector=s=>s==='[data-rv-original]'?original:s==='[data-rv-status]'?status:s==='[data-rv-copy-source]'?source:null;
 root.querySelectorAll=s=>s==='[data-rv-copy]'?[record,link]:[];
 const document={querySelector:s=>s==='[data-record-viewer]'?root:null,createElement:tag=>new Element(tag)};
 const location={href:'http://localhost/exp/example/cell/II%7Cmid/raw?mode=json'};
 const history={replaceState:(state,title,url)=>{location.href=String(url)}};
 let copied=null,blocked=false;
 const navigator={clipboard:{writeText:async text=>{if(blocked)throw new Error('denied');copied=text;}}};
 vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{document,location,history,navigator,URL});
 await record.events.click();
 assert.equal(copied,exactText,'original text must not be JSON-reserialized');
 assert.match(status.textContent,/原文をコピーしました/);
 await link.events.click();
 const url=new URL(copied);
 assert.equal(url.searchParams.get('line'),'7');
 assert.equal(url.searchParams.get('source'),'a'.repeat(64));
 assert.equal(url.searchParams.get('mode'),'json');
 assert.equal(url.hash,'#L7');
 blocked=true;
 await record.events.click();
 assert.match(status.textContent,/できません/);
 const dialog=root.children.at(-1),area=dialog.children.find(n=>n.tag==='textarea');
 assert.equal(dialog.open,true);
 assert.equal(area.value,exactText);
 assert.equal(area.readOnly,true);
 assert.equal(area.selected,true);
 dialog.close();
 assert.equal(record.focused,true);
 assert.equal(dialog.removed,true);
 console.log('original copy, bound link, denied clipboard fallback: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
