// Run: node test_confirm_ui_structure.js
const assert = require('node:assert/strict');
const ui = require('../scripts/confirm_ui/static/app.js');
const template = {id: 'test', defaults: {}, director_taskbook: {}};
ui.VISUAL_FIELDS.forEach(field => { template.defaults[field] = 'value'; });
ui.TASKBOOK_FIELDS.forEach(field => { template.director_taskbook[field] = 'value'; });
function create(composition) {
  return ui.goNext(ui.goNext(ui.createState([template], 'test', 4, null, '', '', composition)));
}
const pages = [
  {composition_page_id: 'structure:cover', page_role: 'cover', fixed_page_title: '报告', source_preview: '来源'},
  {composition_page_id: 'structure:toc', page_role: 'toc', fixed_page_title: '目录'},
  {composition_page_id: 'structure:section:1', page_role: 'section', fixed_page_title: '第一章'},
  {composition_page_id: 'source:1', page_role: 'content', source_page_number: 1, material_source_block_ids: ['p1']},
  {composition_page_id: 'source:2', page_role: 'content', source_page_number: 2, material_source_block_ids: ['p2']},
  {composition_page_id: 'structure:closing', page_role: 'closing', fixed_page_title: '谢谢'}
];
const original = JSON.stringify(pages);
let state = create({pages, page_count: 6, warnings: []});
let payload = ui.buildSubmission(state, 'test');
assert.equal(payload.structure_confirmed, true);
assert.equal(payload.confirmed_pages.length, 6);
assert.equal(payload.revision, 4);
assert.equal('source_preview' in payload.confirmed_pages[0], false);
assert.throws(() => ui.selectStructurePage(state, 'source:1', false), /only added/);
assert.throws(() => ui.selectStructurePage(state, 'missing', false), /only added/);
assert.throws(() => ui.selectStructurePage(ui.goBack(state), 'structure:cover', false), /step 3/);
for (const page of pages.filter(page => page.composition_page_id.startsWith('structure:'))) {
  state = ui.selectStructurePage(state, page.composition_page_id, false);
}
payload = ui.buildSubmission(state, 'test');
assert.deepEqual(payload.confirmed_pages.map(page => page.source_page_number), [1, 2]);
assert.deepEqual(payload.confirmed_pages.map(page => page.output_page_number), [1, 2]);
assert.deepEqual(payload.confirmed_pages.map(page => page.material_source_block_ids), [['p1'], ['p2']]);
state = ui.selectStructurePage(state, 'structure:cover', true);
state = ui.goNext(ui.goBack(state));
assert.equal(ui.buildSubmission(state, 'test').confirmed_pages.length, 3);
assert.equal(JSON.stringify(pages), original, 'input composition must remain unchanged');
assert.equal('confirmed_pages' in ui.buildSubmission(create(), 'legacy'), false);
assert.equal('structure_confirmed' in ui.buildSubmission(create(), 'legacy'), false);
console.log('structure UI assertions passed');
