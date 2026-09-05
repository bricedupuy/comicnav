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
    setAttribute(key,value) {this[key]=value},
    querySelector(selector) {return controls.get(selector.match(/data-meta="([^"]+)"/)?.[1])},
    createTHead() {const child=element();this.append(child);return child},
    createTBody() {const child=element();this.append(child);return child},
    insertRow() {const child=element();this.append(child);return child},
    insertCell() {const child=element();this.append(child);return child},
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
function setControl(key, value) {controls.set(key, {dataset: {meta: key}, value,parentElement:{querySelector:()=>element()}})}

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
async function testComicVine() {
  run(`projectMetadata=normalizeProjectMetadata({series:'Example',number:'25',language_iso:'fr'});resetGcdLookup()`);
  elements.get('metadataProvider').value='comicvine';
  elements.get('metadataProvider').onchange();
  assert.equal(elements.get('gcdLanguage').disabled,true);
  assert.match(elements.get('metadataProviderNotice').textContent,/cannot verify or filter by language/);
  const urls=[];
  sandbox.fetch=async url=>{
    urls.push(new URL(url,'https://comicnav.test'));
    return {ok:true,json:async()=>({candidates:[],page:1,next_page:2,stage:'volumes',notice:'Language unverified'})};
  };
  await run('searchGcd()');
  assert.equal(urls[0].pathname,'/v1/metadata/comicvine/search');
  assert.equal(urls[0].searchParams.has('language'),false);
  await run('searchGcd(false,12)');
  assert.equal(urls[1].searchParams.get('volume_id'),'12');
  assert.equal(elements.get('metadataBack').hidden,false);
  await run('searchGcd(true)');
  assert.equal(urls[2].searchParams.get('volume_id'),'12');
  assert.equal(urls[2].searchParams.get('page'),'2');
  await elements.get('metadataBack').onclick();
  assert.equal(urls[3].searchParams.has('volume_id'),false);

  // Exercise the actual comparison and selective import UI, not just pending state.
  controls.clear();
  setControl('title','My title');setControl('publisher','');setControl('language_iso','fr');
  run(`projectMetadata=normalizeProjectMetadata({title:'My title',language_iso:'fr',sources:{title:'manual',language_iso:'manual'}})`);
  sandbox.fetch=async()=>({ok:true,json:async()=>({fields:{title:'Provider title',publisher:'Publisher'},
    record_id:'comicvine:issue:123',record:{provider:'comicvine',source_url:'https://comicvine.gamespot.com/example/4000-123/'}})});
  await run("compareGcd(123,'comicvine')");
  const comparison=elements.get('gcdComparison');
  assert.equal(comparison.children[0].textContent,'View Comic Vine issue 123');
  const rows=comparison.children[1].children[1].children;
  assert.equal(rows[0].children[0].children[0].checked,false); // existing title
  assert.equal(rows[1].children[0].children[0].checked,true); // empty publisher
  comparison.children[2].onclick();
  run('applyMetadataForm()');
  const saved=json('metadataDraft()');
  assert.equal(saved.title,'My title');assert.equal(saved.language_iso,'fr');
  assert.equal(saved.publisher,'Publisher');assert.equal(saved.sources.publisher,'comicvine');
  assert.equal(saved.field_provenance.publisher.record_id,'comicvine:issue:123');
  const before=json('metadataDraft()');
  setControl('title','Discard Comic Vine edit');run('closeMetadata()');
  assert.deepEqual(json('metadataDraft()'),before);
  elements.get('metadataProvider').value='gcd';elements.get('metadataProvider').onchange();
  assert.equal(elements.get('gcdLanguage').disabled,false);
  assert.equal(elements.get('gcdLanguage').value,'fr');
  assert.equal(json('gcdSearchParams'),null);
}
testLanguageSearch().then(testComicVine).then(()=>console.log('Editor scripts parse; GCD/Comic Vine search, pagination, comparison, selective apply and cancel pass.')).catch(error=>{console.error(error);process.exitCode=1});
