'use strict';
// Test the actual Sidebar.svelte expressions rather than a duplicate implementation.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/components/layout/Sidebar.svelte'), 'utf8');
const defaults = source.match(/const DEFAULT_PINNED_ITEMS = ([^;]+);/);
const pinned = source.match(/\$:\s*pinnedItems = ([\s\S]*?);/);
const visibility = source.match(/const isMenuItemVisible = \(id\) => \{([\s\S]*?)\n\t\};/);
assert.ok(defaults && pinned && visibility, 'Sidebar source anchors must exist');

function evaluate(settings, role = 'admin', allowed = false, enabled = true) {
  const sandbox = {
    $settings: settings,
    $user: { role, permissions: { features: { automations: allowed } } },
    $config: { features: { enable_automations: enabled } },
  };
  const script = `
    const DEFAULT_PINNED_ITEMS = ${defaults[1]};
    const pinnedItems = ${pinned[1]};
    const isMenuItemVisible = (id) => {${visibility[1]}\n};
    JSON.stringify({items: pinnedItems, visible: Boolean(isMenuItemVisible('automations'))});
  `;
  return JSON.parse(vm.runInNewContext(script, sandbox));
}

test('new accounts show Automations after Workspace', () => {
  assert.deepEqual(evaluate(undefined), { items: ['notes', 'workspace', 'automations'], visible: true });
});
test('existing saved menus gain Automations without mutating settings', () => {
  const settings = { pinnedMenuItems: ['notes', 'workspace'] };
  assert.deepEqual(evaluate(settings).items, ['notes', 'workspace', 'automations']);
  assert.deepEqual(settings.pinnedMenuItems, ['notes', 'workspace']);
});
test('existing Automations entry is not duplicated or reordered', () => {
  const items = ['notes', 'automations', 'calendar', 'workspace'];
  assert.deepEqual(evaluate({ pinnedMenuItems: items }).items, items);
});
test('custom order and additional entries are preserved', () => {
  assert.deepEqual(evaluate({ pinnedMenuItems: ['calendar', 'workspace', 'notes'] }).items,
    ['calendar', 'workspace', 'notes', 'automations']);
});
test('an empty saved menu still includes Automations', () => {
  assert.deepEqual(evaluate({ pinnedMenuItems: [] }).items, ['automations']);
});
test('duplicate pinned entries cannot produce duplicate keyed menu items', () => {
  assert.deepEqual(evaluate({ pinnedMenuItems: ['automations', 'notes', 'automations'] }).items,
    ['automations', 'notes']);
});
test('authorized regular users can see the entry', () => {
  assert.equal(evaluate({}, 'user', true).visible, true);
});
test('unauthorized users cannot see the entry', () => {
  assert.equal(evaluate({}, 'user', false).visible, false);
});
test('disabled feature stays hidden even for administrators', () => {
  assert.equal(evaluate({}, 'admin', true, false).visible, false);
});
test('expanded and collapsed sidebar both use the same menu list', () => {
  assert.equal((source.match(/\{#each pinnedItems as itemId \(itemId\)\}/g) || []).length, 2);
  assert.ok(source.indexOf('id="pinned-menu-items-list"') < source.indexOf('name="Projects"'));
  assert.match(source, /automations:\s*\{ label: \$i18n\.t\('Automations'\), href: '\/automations'/);
});
