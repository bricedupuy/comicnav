// Run with Node: node tests/test_editor_metadata.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../web/editor.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(match => match[1]).filter(Boolean);
for (const script of scripts) new vm.Script(script); // Parse every inline script, not just the new functions.
const script = scripts.find(script => script.includes('const metadataFields='));
const metadataCode = script.slice(script.indexOf('const metadataFields='), script.indexOf('const reviewMeta='));
const controls = new Map();
const elements = new Map();
function element() {
  return {children: [], dataset: {}, value: '', textContent: '', hidden: false,
    append(...children) {this.children.push(...children)},
    appendChild(child) {this.children.push(child)},
    replaceChildren() {this.children = []},
    focus() {}, reportValidity() {return true}, querySelectorAll() {return [...controls.values()]},
  };
}
const sandbox = vm.createContext({
  console, URLSearchParams, AbortController,
  document: {createElement: element},
  window: {addEventListener() {}},
  $: id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id)},
  pages: new Array(48), setStatus() {},
});
vm.runInContext(metadataCode, sandbox);
function run(code) {return vm.runInContext(code, sandbox)}
function json(code) {return JSON.parse(JSON.stringify(run(code)))}
function setControl(key, value) {controls.set(key, {dataset: {meta: key}, value})}

run(`projectMetadata=normalizeProjectMetadata({series:'Original',title:'Manual title',release_group:'TONER',sources:{title:'manual'}}); renderMetadataForm()`);
assert.equal(elements.get('metadataFields').children.length, 33);
setControl('title', 'Manual title');
setControl('series', 'Selected series');
setControl('release_group', 'TONER');
run(`gcdPendingFields={series:{value:'Selected series',record_id:'gcd:issue:123',record:{provider:'gcd'}}}; applyMetadataForm()`);
assert.equal(json('metadataDraft()').title, 'Manual title');
assert.equal(json('metadataDraft()').sources.title, 'manual');
assert.equal(json('metadataDraft()').sources.series, 'gcd');
assert.equal(json('metadataDraft()').field_provenance.series.original_value, 'Selected series');
assert.equal(json('metadataDraft()').release_group, 'TONER');

// Editing after copying a suggestion must become manual, with stale provenance removed.
setControl('series', 'My correction');
run('applyMetadataForm()');
assert.equal(json('metadataDraft()').sources.series, 'manual');
assert.deepEqual(json('metadataDraft()').field_provenance, {});
assert.deepEqual(json('metadataDraft()').provider_records, {});

// Cancel never applies copied fields to project state.
const before = json('metadataDraft()');
setControl('title', 'Discard this title');
run('closeMetadata()');
assert.deepEqual(json('metadataDraft()'), before);
// Language is prefilled, sent to the API, and retained on subsequent result pages.
async function testLanguageSearch() {
  run(`projectMetadata=normalizeProjectMetadata({series:'Example',number:'25',language_iso:'fr'});resetGcdLookup()`);
  assert.equal(elements.get('gcdLanguage').value, 'fr');
  const urls = [];
  sandbox.fetch = async url => {
    urls.push(new URL(url, 'https://comicnav.test'));
    return {ok:true,json:async()=>({candidates:[],page:urls.length,next_page:urls.length===1?2:null,language:'fr',filtered_count:1,unknown_language_count:0})};
  };
  await run('searchGcd()');
  assert.equal(urls[0].searchParams.get('language'), 'fr');
  assert.equal(elements.get('gcdMore').hidden, false);
  assert.match(elements.get('gcdStatus').textContent, /later pages may contain matches/);
  elements.get('gcdLanguage').value='nl';
  await run('searchGcd(true)');
  assert.equal(urls[1].searchParams.get('language'), 'fr');
  assert.equal(urls[1].searchParams.get('page'), '2');
  elements.get('gcdLanguage').value='';
  await run('searchGcd()');
  assert.equal(urls[2].searchParams.has('language'), false);
  run(`projectMetadata=normalizeProjectMetadata({});resetGcdLookup()`);
  assert.equal(elements.get('gcdLanguage').value, '');
}
testLanguageSearch().then(()=>console.log('Editor scripts parse; metadata apply/cancel and language search/pagination pass.')).catch(error=>{console.error(error);process.exitCode=1});
