'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {EXT_DIR} = require('./gnome_sandbox.js');

test('R-ext-1: metadata and module shape', () => {
    const meta = JSON.parse(fs.readFileSync(path.join(EXT_DIR, 'metadata.json'), 'utf8'));
    assert.equal(meta.uuid, 'atree@atree');
    assert.equal(meta.name, 'atree Desktop');
    assert.equal(meta.description,
        'Runs atree, the map of coding-agent sessions, as a live see-through widget on the desktop.');
    assert.deepEqual(meta['shell-version'], ['45', '46', '47', '48', '49', '50', '51']);
    const src = fs.readFileSync(path.join(EXT_DIR, 'extension.js'), 'utf8');
    assert.match(src, /export default class \w+ extends Extension/);
});
