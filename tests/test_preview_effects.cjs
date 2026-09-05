// Run with Node: node tests/test_preview_effects.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../web/editor.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(Boolean);
for (const script of scripts) new vm.Script(script);
const source = scripts.find(script => script.includes('function renderOutsideEffects('));
function node(tag) {
  return {tag, attrs:{}, children:[], setAttribute(key,value){this.attrs[key]=String(value)},
    append(...children){this.children.push(...children)}, appendChild(child){this.children.push(child)}};
}
function addSvg(tag, attrs, parent) {
  const child=node(tag);
  for (const [key,value] of Object.entries(attrs)) child.setAttribute(key,value);
  parent.appendChild(child);
  return child;
}
function find(root, tag) {return [root,...root.children.flatMap(child=>find(child))].filter(n=>!tag||n.tag===tag)}
const page={src:'test-page',w:1000,h:1500,panels:[{points:[[100,100],[500,100],[500,600],[100,600]]}]};
const entries=[{page,x:0,y:0,w:1000,h:1500},{page:{...page,src:'right-page'},x:1000,y:0,w:1000,h:1500}];
const sandbox=vm.createContext({document:{createElementNS:(_,tag)=>node(tag)},addSvg,
  previewPanelIdx:0,previewOverlayMode:'normal',previewBwOpacity:.9,previewDarkOpacity:.74,
  previewFadedOpacity:.58,previewFocusFeather:2,page,entries,layout:{width:2000,height:1500,entries},
});
const run=code=>vm.runInContext(code,sandbox);
run(source.slice(source.indexOf('let previewBlurEnabled='),source.indexOf('function identityCamera(')));
run(source.slice(source.indexOf('function spreadPanelPoints('),source.indexOf('function startPreviewAtPanel(')));
run(source.slice(source.indexOf('function renderSpreadPreviewOverlay('),source.indexOf('function applySpreadPreviewCamera(')));
for(const spread of [false,true]) for(const mode of ['normal','bw','dark','faded']) for(const blur of [false,true]) {
  sandbox.camera=node('g');sandbox.previewOverlayMode=mode;
  run(`previewBlurEnabled=${blur};previewBlurStrength=.4`);
  run(spread?'renderSpreadPreviewOverlay(camera,layout,entries[0])':'renderPreviewOverlay(camera,page)');
  const all=find(sandbox.camera), images=find(sandbox.camera,'image');
  assert.equal(images.length,blur||mode==='bw'?(spread?2:1):0);
  const effectFilter=find(sandbox.camera,'filter').find(n=>n.attrs.id.endsWith('Effects'));
  assert.equal(Boolean(effectFilter),blur||mode==='bw');
  if(effectFilter) {
    assert.equal(find(effectFilter,'feGaussianBlur').length,blur?1:0);
    const alpha=find(effectFilter,'feFuncA');
    assert.equal(alpha.length,blur?1:0);
    if(blur){assert.equal(alpha[0].attrs.intercept,'1');assert.equal(alpha[0].attrs.slope,'0')}
    const matrix=find(effectFilter,'feColorMatrix');
    assert.equal(matrix.length,mode==='bw'?1:0);
    if(matrix.length) assert.ok(Math.abs(Number(matrix[0].attrs.values)-(blur?.1:0))<1e-9);
    for(const image of images) assert.equal(Number(image.attrs.opacity),blur?1:.9);
    assert.ok(all.some(n=>n.tag==='g'&&n.attrs.mask===`url(#${spread?'spreadPreviewOutsideMask':'previewOutsideMask'})`));
  }
  const tints=find(sandbox.camera,'rect').filter(n=>n.attrs['fill-opacity']);
  assert.equal(tints.length,mode==='dark'||mode==='faded'?1:0);
  if(tints.length) assert.equal(Number(tints[0].attrs['fill-opacity']),mode==='dark'?.74:.58);
  assert.equal(find(sandbox.camera,'polygon')[0].attrs.filter,`url(#${spread?'spreadPreviewFocusFeather':'previewFocusFeather'})`);
}
// Zero strength is a no-op even with the checkbox on; overviews stay untouched.
sandbox.camera=node('g');sandbox.previewOverlayMode='normal';
run('previewBlurEnabled=true;previewBlurStrength=0;renderPreviewOverlay(camera,page)');
assert.equal(find(sandbox.camera,'image').length,0);
for(const spread of [false,true]) {
  sandbox.camera=node('g');sandbox.previewPanelIdx=-1;
  run('previewBlurStrength=.4');
  run(spread?'renderSpreadPreviewOverlay(camera,layout,entries[0])':'renderPreviewOverlay(camera,page)');
  assert.equal(sandbox.camera.children.length,0);
}
assert.match(html,/id="previewBlurEnabled" type="checkbox"/);
assert.match(html,/bindPreviewRange\('previewBlurStrength'/);
assert.match(html,/id="previewBlurStrength" type="range" min="0" max="1" step="0.05"/);
console.log('Preview scripts parse; blur combinations, feather masks, spread coverage, zero strength and overview checks pass.');
