'use strict';
// Run with:  node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {makeWorld, DATA_DIR} = require('./gnome_sandbox.js');

const CONFIG = path.join(DATA_DIR, 'atree', 'extension.json');

function withConfig(text, opts = {}) {
    const files = {};
    if (text !== undefined)
        files[CONFIG] = text;
    return makeWorld(Object.assign({files}, opts));
}

const GOOD = JSON.stringify({python: '/opt/my venv/bin/python', app: '/home/me/My Apps/atree.py'});

test('R-ext-4: invalid configs log one error and spawn nothing', () => {
    const bad = [undefined, 'null', '[]', '[1,2]', '{"python": "", "app": "/x"}', '{"python": "/p"}',
        '{"python": "  ", "app": "/x"}', '{"python": 3, "app": "/x"}', '{not json', '"text"'];
    for (const text of bad) {
        const w = withConfig(text);
        const ext = w.make();
        assert.doesNotThrow(() => ext.enable(), `config ${text}`);
        assert.equal(w.errors.length, 1, `config ${text}`);
        assert.match(w.errors[0], /atree\.py --install-extension/);
        assert.deepEqual(w.launches, []);
        assert.deepEqual({...ext._config}, {});
        assert.doesNotThrow(() => ext._spawn());
        ext._config = null;
        assert.doesNotThrow(() => ext._spawn());
        assert.deepEqual(w.launches, []);
        ext.disable();
    }
});

test('R-ext-4: a valid config is accepted silently', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    assert.equal(w.errors.length, 0);
    assert.equal(w.launches.length, 1);
    ext.disable();
});

test('R-ext-5: argv is exactly python, app, --widget (X11)', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    assert.deepEqual(w.launches, [['/opt/my venv/bin/python', '/home/me/My Apps/atree.py', '--widget']]);
    assert.equal(ext._client, null);
    ext.disable();
});

test('R-ext-5/6: on Wayland the widget is spawned through a Wayland client', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    assert.deepEqual(w.launches, [['/opt/my venv/bin/python', '/home/me/My Apps/atree.py', '--widget']]);
    assert.ok(ext._client);
    ext.disable();
});

test('G-core-4: without Meta.is_wayland_compositor (GNOME 49+) the Wayland client path is taken', () => {
    const w = withConfig(GOOD, {waylandCheck: false});
    const ext = w.make();
    ext.enable();
    assert.deepEqual(w.errors, []);
    assert.equal(w.launches.length, 1);
    assert.ok(ext._client);
    ext.disable();
});

test('R-ext-7/8: exit respawns after 5 s; disable cancels and stops', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    w.procs[0].exit();
    assert.equal(w.launches.length, 1);
    w.advance(4999);
    assert.equal(w.launches.length, 1);
    w.advance(1);
    assert.equal(w.launches.length, 2);
    ext.disable();
    assert.equal(w.procs[1].forced, 1);
    assert.equal(w.pending(), 0);
    assert.equal(w.global.display.count(), 0);
    assert.equal(w.global.window_manager.count(), 0);
    w.procs[1].exit();
    w.advance(10000);
    assert.equal(w.launches.length, 2);
});

test('R-ext-9: foreign, transient and non-normal windows are ignored', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    w.map(w.makeWindow({pid: '1'}));
    w.map(w.makeWindow({transient: {}}));
    w.map(w.makeWindow({type: w.Meta.WindowType.DIALOG}));
    assert.equal(ext._window, null);
    const mine = w.makeWindow();
    w.map(mine);
    assert.equal(ext._window, mine);
    ext.disable();
});

test('R-ext-13: a window that never pins: at most 6 timeouts, one warning, then quiet', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow({canStick: false});
    w.map(win);
    const before = w.timeoutsRun;
    w.advance(60000);
    assert.ok(w.timeoutsRun - before <= 6, `ran ${w.timeoutsRun - before} timeouts`);
    assert.equal(w.warnings.length, 1);
    assert.match(w.warnings[0], /giving up until the work area changes/);
    assert.equal(w.pending(), 0);
    // later non-fresh checks stay quiet
    w.global.display.emit('restacked');
    w.advance(60000);
    assert.equal(w.warnings.length, 1);
    assert.equal(w.pending(), 0);
    // a fresh check starts over
    w.global.display.emit('workareas-changed');
    assert.equal(w.pending(), 1);
    w.advance(60000);
    assert.equal(w.warnings.length, 2);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('R-ext-13: checks are coalesced and rate limited after a pin', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    w.map(win);
    assert.equal(w.pending(), 1);
    w.global.display.emit('restacked');
    w.global.display.emit('restacked');
    assert.equal(w.pending(), 1);
    w.advance(999);
    assert.equal(w.pending(), 1);
    w.advance(1);
    // pinned on X11: sticky + placed, type cannot be set
    assert.equal(w.pending(), 0);
    assert.equal(ext._tries, 0);
    assert.ok(win.sticky);
    assert.equal(win.get_layer(), w.Meta.StackLayer.NORMAL);
    assert.ok(win.lowered >= 1);
    ext.disable();
});

test('R-ext-11: without set_type the window is hidden from the window list and sunk', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    const app = w.makeWindow({pid: '8'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    assert.equal(win.type, w.Meta.WindowType.NORMAL);
    assert.equal(win.get_layer(), w.Meta.StackLayer.NORMAL);
    assert.ok(win.skipTaskbar);
    assert.equal(win.raised, 0);
    assert.deepEqual(w.global.display.stacked(), [win, app]);
    w.advance(2000);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('R-ext-12: DING windows hidden from get_window_actors are still found and kept beneath', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    w.advance(2000);
    // DING maps later and lowers itself, which puts it under nothing but covers the widget
    const ding = w.makeWindow({title: '@!0,0;BDHF', pid: '7'});
    ding.lower();
    assert.ok(!w.global.get_window_actors().some(a => a.meta_window === ding));
    w.global.display.stack.splice(w.global.display.stack.indexOf(ding), 1);
    w.global.display.stack.splice(w.global.display.stack.indexOf(win) + 1, 0, ding);
    assert.ok(!ext._aboveDesktop(win));
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.deepEqual(w.global.display.stacked(), [ding, win]);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('R-ext-12: only DING windows flagged B (keep at bottom) are pushed beneath', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    const ding = w.makeWindow({title: '@!0,0;BDHF', pid: '7'});
    const dialog = w.makeWindow({title: '@!0,0;DHF', pid: '7'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    assert.deepEqual(w.global.display.stacked(), [ding, win, dialog]);
    ext.disable();
});

test('lowering another app restores DING < widget < apps', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    const ding = w.makeWindow({title: '@!0,0;BDHF', pid: '7'});
    const app = w.makeWindow({pid: '8'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    w.advance(2000);
    app.lower();
    assert.deepEqual(w.global.display.stacked(), [app, ding, win]);
    assert.equal(ext._isPinned(), false);
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.deepEqual(w.global.display.stacked(), [ding, win, app]);
    assert.equal(ext._isPinned(), true);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('set_type keeps the widget above desktop DING and below lowered apps', () => {
    const w = withConfig(GOOD, {wayland: true, setType: true});
    const ext = w.make();
    ext.enable();
    const ding = w.makeWindow({title: '@!0,0;BDHF', pid: '7',
        type: w.Meta.WindowType.DESKTOP, layer: w.Meta.StackLayer.DESKTOP});
    const app = w.makeWindow({pid: '8'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    w.advance(2000);
    assert.equal(win.type, w.Meta.WindowType.DESKTOP);
    app.lower();
    assert.deepEqual(w.global.display.stacked(), [ding, win, app]);
    assert.equal(ext._isPinned(), true);
    // losing the desktop type must be repaired even if the stack still happens to be right
    win.set_type(w.Meta.WindowType.NORMAL);
    assert.equal(ext._isPinned(), false);
    win.emit('notify::window-type');
    w.advance(2000);
    assert.equal(win.type, w.Meta.WindowType.DESKTOP);
    // DING may switch to its normal layer after the widget maps
    ding.type = w.Meta.WindowType.NORMAL;
    ding.layer = w.Meta.StackLayer.NORMAL;
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.equal(win.type, w.Meta.WindowType.NORMAL);
    assert.deepEqual(w.global.display.stacked(), [ding, win, app]);
    app.lower();
    assert.equal(ext._isPinned(), false);
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.deepEqual(w.global.display.stacked(), [ding, win, app]);
    assert.equal(ext._isPinned(), true);
    assert.equal(w.pending(), 0);
    assert.deepEqual(w.errors, []);
    ext.disable();
});

test('desktop windows on the widget monitor stay beneath it, including after restacking', () => {
    const w = withConfig(GOOD, {wayland: true, setType: true});
    const ext = w.make();
    ext.enable();
    const desktop = w.makeWindow({pid: '6', type: w.Meta.WindowType.DESKTOP,
        layer: w.Meta.StackLayer.DESKTOP});
    const remote = w.makeWindow({pid: '7', monitor: 1, type: w.Meta.WindowType.DESKTOP,
        layer: w.Meta.StackLayer.DESKTOP});
    const app = w.makeWindow({pid: '8'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    w.advance(2000);
    assert.equal(win.type, w.Meta.WindowType.DESKTOP);
    assert.deepEqual(w.global.display.stacked(), [desktop, win, remote, app]);
    assert.equal(remote.lowered, 0);
    assert.equal(ext._isPinned(), true);
    desktop.raise();
    assert.equal(ext._isPinned(), false);
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.deepEqual(w.global.display.stacked(), [desktop, win, remote, app]);
    assert.equal(ext._isPinned(), true);
    assert.equal(remote.lowered, 0);
    assert.equal(w.pending(), 0);
    assert.deepEqual(w.errors, []);
    assert.deepEqual(w.warnings, []);
    ext.disable();
});

test('R-ext-11: a failing step does not stop the others', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    win.stick = () => {
        throw new Error('boom');
    };
    w.map(win);
    assert.equal(win.resizes.length, 1);
    assert.ok(win.lowered >= 1);
    assert.ok(w.errors.some(e => /boom/.test(e)));
    ext.disable();
});

test('R-ext-14: no work area means no placement and no monitor lookup for a negative index', () => {
    const w = withConfig(GOOD, {primaryIndex: -1});
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    w.map(win);
    w.advance(20000);
    assert.equal(w.areaCalls, 0);
    assert.equal(win.resizes.length, 0);
    ext.disable();

    const w2 = withConfig(GOOD, {area: null});
    const ext2 = w2.make();
    ext2.enable();
    const win2 = w2.makeWindow();
    w2.map(win2);
    w2.advance(20000);
    assert.ok(w2.areaCalls > 0);
    assert.equal(win2.resizes.length, 0);
    ext2.disable();
});

test('R-ext-14: placing reads the area once and resizes only when the frame differs', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    ext._window = win;
    w.areaCalls = 0;
    ext._place();
    assert.equal(w.areaCalls, 1);
    assert.deepEqual(win.resizes, [[false, 0, 32, 1920, 1048]]);
    ext._place();
    assert.equal(win.resizes.length, 1);
    w.area = {x: 0, y: 40, width: 1920, height: 1040};
    ext._place();
    assert.deepEqual(win.resizes[1], [false, 0, 40, 1920, 1040]);
    ext._place();
    assert.equal(win.resizes.length, 2);
    ext._window = null;
    ext.disable();
});

test('R-ext-15: a monitor event with an area places the window once and settles', () => {
    const w = withConfig(GOOD, {area: null});
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    w.map(win);
    w.advance(60000);
    assert.equal(win.resizes.length, 0);
    w.area = {x: 0, y: 27, width: 2560, height: 1413};
    w.layoutManager.emit('monitors-changed');
    w.advance(60000);
    assert.deepEqual(win.resizes, [[false, 0, 27, 2560, 1413]]);
    assert.equal(ext._tries, 0);
    assert.equal(w.pending(), 0);
    w.layoutManager.emit('monitors-changed');
    w.advance(60000);
    assert.equal(win.resizes.length, 1);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('R-ext-10: unmanaged forgets the window; being raised sinks it again', () => {
    const w = withConfig(GOOD);
    const ext = w.make();
    ext.enable();
    const win = w.makeWindow();
    w.map(win);
    const lowered = win.lowered;
    win.emit('raised');
    assert.equal(win.lowered, lowered + 1);
    win.emit('unmanaged');
    assert.equal(ext._window, null);
    assert.equal(win.count(), 0);
    assert.equal(w.pending(), 0);
    ext.disable();
});

test('G-log-1: clicking the widget (GNOME 46, DING hidden from actors) keeps DING < widget < apps', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    const ding = w.makeWindow({title: '@!0,0;BDHF', pid: '7'});
    const other = w.makeWindow({pid: '8'});
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    w.advance(2000);
    assert.deepEqual(w.errors, []);
    assert.deepEqual(w.global.display.stacked(), [ding, win, other]);
    // a click raises it over the user's windows; it sinks back above DING at once
    win.raise();
    win.emit('raised');
    assert.deepEqual(w.global.display.stacked(), [ding, win, other]);
    // DING lowering itself again (its own idle refresh) leaves the order intact
    ding.lower();
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.deepEqual(w.global.display.stacked(), [ding, win, other]);
    // "always on top" is undone by the next check
    win.above = true;
    w.global.display.emit('restacked');
    w.advance(2000);
    assert.equal(win.above, false);
    assert.deepEqual(w.global.display.stacked(), [ding, win, other]);
    assert.equal(ext._tries, 0);
    ext.disable();
});

test('Resume: Activate raises the nearest pid\'s most recently used window, never the widget', () => {
    const w = withConfig(GOOD, {wayland: true});
    const ext = w.make();
    ext.enable();
    assert.equal(w.exported.path, '/org/atree/Desktop');
    assert.equal(w.exported.bus, 'session-bus');
    assert.match(w.exported.xml, /interface name="org.atree.Desktop"/);
    const win = w.makeWindow();
    ext._client.windows.add(win);
    w.map(win);
    const old = w.makeWindow({pid: '500', userTime: 1});
    const recent = w.makeWindow({pid: '500', userTime: 9});
    w.makeWindow({pid: '600', userTime: 50});
    w.makeWindow({pid: '700', type: w.Meta.WindowType.DIALOG});
    const activate = w.exported.impl.Activate;
    assert.equal(activate([123, 500, 600]), true);
    assert.deepEqual(w.activated, [recent]);
    assert.equal(activate([700, 4242, 999]), false);
    assert.equal(activate([]), false);
    assert.deepEqual(w.activated, [recent]);
    assert.ok(old);
    ext.disable();
    assert.equal(w.exported, null);
});
