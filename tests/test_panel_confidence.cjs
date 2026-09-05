const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../web/editor.html'),'utf8');
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]).filter(Boolean);
scripts.forEach(code=>new vm.Script(code));
const source=scripts.find(code=>code.includes('const panelConfidenceCutoffs='));
function element(){return {children:[],style:{},classList:{add(){},remove(){}},
  appendChild(child){this.children.push(child)},set innerHTML(value){this.children=[];this.html=value},get innerHTML(){return this.html},
  querySelector(){return element()},querySelectorAll(){return this.children}}}
const elements=new Map();
const sandbox=vm.createContext({WeakMap,Number,JSON,Math,
  $:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)},
  document:{createElement:element},pages:[],current:0,selected:-1,hoveredPanel:-1,previewMode:false,
  rowDragIdx:null,render(){vm.runInContext('updatePanelConfidenceUI()',sandbox)},setStatus(message){sandbox.status=message},
  isRect:()=>true,move(){},reorderPanel(){},
});
const run=code=>vm.runInContext(code,sandbox);
run(source.slice(source.indexOf('function snapshotPage('),source.indexOf('function validatePage(')));
run(source.split(/\r?\n/).find(line=>line.startsWith('function pageModelIds(')));
run(source.slice(source.indexOf('const panelConfidenceCutoffs='),source.indexOf('function selectPage(')));
run(source.split(/\r?\n/).find(line=>line.startsWith('function renderPanelList(')));
const panel=confidence=>({points:[[0,0],[10,0],[10,10],[0,10]],confidence,model:'inkwell-yolov8n'});
sandbox.pages=[{panels:[panel(0),panel(.25),panel(.5),panel(1),panel(null),panel(undefined)],reviewStatus:'validated'},
  {panels:[panel(.1)],reviewStatus:'model'}];
run('historyForPage(pages[0]);historyForPage(pages[1])');
const original=run('snapshotPage(pages[0])');
run('selected=1;setPanelConfidenceCutoff(50);renderPanelList()');
assert.equal(run('selected'),-1);
assert.equal(elements.get('panelConfidenceSummary').textContent,'4 of 6 shown · 2 below 50%');
assert.equal(elements.get('removeLowConfidenceBtn').textContent,'Remove 2 panels below 50%');
assert.equal(elements.get('panelList').children.length,4);
assert.match(elements.get('panelList').children[0].innerHTML,/>3<\/span>/); // original index, not filtered index
assert.equal(run('snapshotPage(pages[0])'),original); // slider does not mutate drafts or history
assert.equal(run('pages[0].history.undo.length'),0);
run('current=1;updatePanelConfidenceUI()');
assert.equal(elements.get('panelConfidenceValue').textContent,'0%');
run('current=0;selected=3;removeLowConfidencePanels()');
assert.equal(run('pages[0].panels.length'),4);
assert.equal(run('pages[0].panels[0].confidence'),.5); // equality kept
assert.equal(run('pages[0].reviewStatus'),'customized');
assert.equal(run('selected'),1);
assert.equal(run('pages[0].history.undo.length'),1);
assert.equal(run('panelConfidenceCutoff()'),0); // restored panels visible on undo
assert.equal(run('pages[1].panels.length'),1);
run('removeLowConfidencePanels()');
assert.equal(run('pages[0].history.undo.length'),1); // no-op, no spurious undo
run('undoPageEdit()');
assert.equal(run('snapshotPage(pages[0])'),original);
run('redoPageEdit()');
assert.equal(run('pages[0].panels.length'),4);
run('current=1;setPanelConfidenceCutoff(100);removeLowConfidencePanels()');
assert.equal(run('pages[1].panels.length'),0);
assert.equal(run('pages[1].reviewStatus'),'empty');
assert.equal(run('pages[1].modelIds[0]'),'inkwell-yolov8n');
run('undoPageEdit();previewMode=true;removeLowConfidencePanels()');
assert.equal(run('pages[1].panels.length'),1);
run('previewMode=false;current=-1;updatePanelConfidenceUI();removeLowConfidencePanels()');
assert.equal(elements.get('panelConfidenceCutoff').disabled,true);
assert.equal(elements.get('removeLowConfidenceBtn').disabled,true);
run('current=0;setPanelConfidenceCutoff(0);renderPanelList()');
// 0% is real confidence, not a missing value.
sandbox.pages[0].panels.unshift(panel(0));run('renderPanelList()');
assert.match(elements.get('panelList').children[0].innerHTML,/class="confidence">0%/);
assert.equal(run('hasPanelConfidence({confidence:null})'),false);
assert.equal(run('hasPanelConfidence({confidence:NaN})'),false);
// Canvas and reading-order arrows use the same predicate as the panel list.
const render=source.split(/\r?\n/).find(line=>line.startsWith('function render(){'));
assert.match(render,/p\.panels\.forEach\(\(x,i\)=>\{if\(belowPanelConfidence\(x,p\)\)return;/);
assert.match(render,/p\.panels\.filter\(x=>!belowPanelConfidence\(x,p\)\)\.map/);
console.log('Confidence filter: per-page preview, exact cutoff, unknown scores, removal, review state, undo/redo and empty-page checks pass.');
