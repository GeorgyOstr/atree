'use strict';
// Loads gnome-extension/atree@atree/extension.js in a Node vm sandbox with fake GNOME APIs.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const EXT_DIR = path.join(__dirname, '..', 'gnome-extension', 'atree@atree');
const DATA_DIR = '/home/me/.local/share';
const SOURCE = fs.readFileSync(path.join(EXT_DIR, 'extension.js'), 'utf8');

class Emitter {
    constructor() {
        this._handlers = new Map();
        this._next = 1;
    }

    connect(name, fn) {
        const id = this._next++;
        this._handlers.set(id, {name, fn});
        return id;
    }

    connect_after(name, fn) {
        return this.connect(name, fn);
    }

    disconnect(id) {
        this._handlers.delete(id);
    }

    emit(name, ...args) {
        for (const {name: n, fn} of [...this._handlers.values()]) {
            if (n === name)
                fn(this, ...args);
        }
    }

    count(name) {
        return [...this._handlers.values()].filter(h => !name || h.name === name).length;
    }
}

function makeWorld({wayland = false, waylandCheck = true, setType = false, files = {}, primaryIndex = 0, area = {x: 0, y: 32, width: 1920, height: 1048}} = {}) {
    const world = {
        clock: 0,
        timeouts: new Map(),
        nextSource: 1,
        timeoutsRun: 0,
        launches: [],
        procs: [],
        errors: [],
        warnings: [],
        logs: [],
        areaCalls: 0,
        primaryIndex,
        area,
        files: Object.assign({}, files),
    };

    const GLib = {
        PRIORITY_DEFAULT: 0,
        PRIORITY_LOW: 300,
        SOURCE_REMOVE: false,
        get_monotonic_time: () => world.clock * 1000,
        build_filenamev: parts => parts.join('/'),
        get_user_data_dir: () => DATA_DIR,
        file_get_contents(file) {
            if (!(file in world.files))
                throw new Error(`No such file ${file}`);
            return [true, new TextEncoder().encode(world.files[file])];
        },
        timeout_add(priority, ms, fn) {
            const id = world.nextSource++;
            world.timeouts.set(id, {at: world.clock + ms, fn, priority});
            return id;
        },
        timeout_add_seconds(priority, s, fn) {
            return GLib.timeout_add(priority, s * 1000, fn);
        },
        source_remove(id) {
            world.timeouts.delete(id);
        },
    };

    class Cancellable {
        constructor() {
            this.cancelled = false;
        }

        cancel() {
            this.cancelled = true;
        }

        is_cancelled() {
            return this.cancelled;
        }
    }

    class FakeProc {
        constructor(argv) {
            this.argv = argv;
            this.pid = String(4242 + world.procs.length);
            this.forced = 0;
            this.waiter = null;
            world.procs.push(this);
        }

        get_identifier() {
            return this.pid;
        }

        wait_async(cancellable, cb) {
            this.waiter = {cancellable, cb};
        }

        wait_finish() {
            return true;
        }

        force_exit() {
            this.forced++;
        }

        exit() {
            const w = this.waiter;
            if (w)
                w.cb(this, null);
        }
    }

    class SubprocessLauncher {
        constructor(props) {
            this.props = props;
        }

        spawnv(argv) {
            world.launches.push([...argv]);
            return new FakeProc([...argv]);
        }
    }

    world.exported = null;  // {xml, impl, bus, path} while the D-Bus object is exported
    world.activated = [];
    const DBusExportedObject = {
        wrapJSObject(xml, impl) {
            const obj = {
                export(bus, path) {
                    world.exported = {xml, impl, bus, path};
                },
                unexport() {
                    world.exported = null;
                },
            };
            return obj;
        },
    };

    const Gio = {SubprocessFlags: {NONE: 0}, SubprocessLauncher, Cancellable, DBusExportedObject,
        DBus: {session: 'session-bus'}};

    class WaylandClient {
        constructor(launcher) {
            this.launcher = launcher;
            this.windows = new Set();
            this.hidden = 0;
            this.desktop = 0;
        }

        static new_subprocess(context, launcher, argv) {
            const c = new WaylandClient(launcher);
            c.proc = launcher.spawnv(argv);
            return c;
        }

        get_subprocess() {
            return this.proc;
        }

        owns_window(win) {
            return this.windows.has(win);
        }

        hide_from_window_list(win) {
            this.hidden++;
            win.skipTaskbar = true;
        }

        make_desktop(win) {
            this.desktop++;
            win.type = Meta.WindowType.DESKTOP;
        }
    }

    const Meta = {
        WindowType: {NORMAL: 0, DESKTOP: 1, DIALOG: 3},
        StackLayer: {DESKTOP: 0, BOTTOM: 1, NORMAL: 2, TOP: 4},
        is_wayland_compositor: () => wayland,
        WaylandClient,
    };
    if (!waylandCheck)  // GNOME 49+
        delete Meta.is_wayland_compositor;

    const sessionMode = new Emitter();
    const layoutManager = new Emitter();
    Object.defineProperty(layoutManager, 'primaryIndex', {get: () => world.primaryIndex});
    layoutManager.getWorkAreaForMonitor = () => {
        world.areaCalls++;
        return world.area ? Object.assign({}, world.area) : null;
    };

    const display = new Emitter();
    // windows in bottom-to-top order within their layer; raise()/lower() move one to the top/bottom
    const stack = [];
    display.stack = stack;
    const layerOf = w => (w.above ? Meta.StackLayer.TOP : w.layer);
    display.sort_windows_by_stacking = list => [...list].sort((a, b) =>
        layerOf(a) - layerOf(b) || stack.indexOf(a) - stack.indexOf(b));
    display.stacked = () => display.sort_windows_by_stacking(stack);
    display.list_all_windows = () => [...stack];
    const windowManager = new Emitter();
    const fakeGlobal = {
        window_manager: windowManager,
        display,
        context: {},
        actors: [],
        // like the real shell with DING on Wayland: its "@!" windows are filtered out here
        get_window_actors: () => fakeGlobal.actors.filter(a => !String(a.meta_window.title ?? '').startsWith('@!')),
    };

    class Extension {
        constructor(meta) {
            this.metadata = meta;
            this.path = meta.path;
        }
    }

    const fakeConsole = {
        error: (...a) => world.errors.push(a.join(' ')),
        warn: (...a) => world.warnings.push(a.join(' ')),
        log: (...a) => world.logs.push(a.join(' ')),
    };

    const code = SOURCE
        .replace(/^import .*$/mg, '')
        .replace('export default class', 'globalThis.__Ext = class');
    const sandbox = {
        GLib, Gio, Meta, Main: {layoutManager, sessionMode, activateWindow: win => world.activated.push(win)},
        Extension, global: fakeGlobal,
        console: fakeConsole, TextDecoder, TextEncoder,
    };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox, {filename: 'extension.js'});

    world.GLib = GLib;
    world.Meta = Meta;
    world.global = fakeGlobal;
    world.layoutManager = layoutManager;
    world.sessionMode = sessionMode;
    world.Ext = sandbox.__Ext;
    world.make = () => new sandbox.__Ext({uuid: 'atree@atree', path: EXT_DIR});

    world.pending = () => world.timeouts.size;

    // Run due timeouts in time order until ``ms`` have passed.
    world.advance = ms => {
        const end = world.clock + ms;
        for (;;) {
            let best = null;
            for (const [id, t] of world.timeouts) {
                if (t.at <= end && (!best || t.at < best[1].at || (t.at === best[1].at && id < best[0])))
                    best = [id, t];
            }
            if (!best)
                break;
            world.clock = Math.max(world.clock, best[1].at);
            world.timeouts.delete(best[0]);
            world.timeoutsRun++;
            if (best[1].fn() === true)
                world.timeouts.set(best[0], {at: world.clock, fn: best[1].fn});
        }
        world.clock = end;
    };

    world.makeWindow = (opts = {}) => {
        const win = new Emitter();
        Object.assign(win, {
            type: Meta.WindowType.NORMAL,
            sticky: false,
            skipTaskbar: false,
            rect: {x: 10, y: 10, width: 800, height: 600},
            resizes: [],
            lowered: 0,
            raised: 0,
            pid: opts.pid ?? '4242',
            transient: null,
            monitor: 0,
            canStick: true,
            layer: Meta.StackLayer.NORMAL,
            above: false,
            title: '',
        }, opts);
        win.get_window_type = () => win.type;
        if (setType) {
            win.set_type = type => {
                win.type = type;
                win.layer = type === Meta.WindowType.DESKTOP ? Meta.StackLayer.DESKTOP : Meta.StackLayer.NORMAL;
            };
        }
        win.get_transient_for = () => win.transient;
        win.get_pid = () => win.pid;
        win.get_monitor = () => win.monitor;
        win.stick = () => {
            if (win.canStick)
                win.sticky = true;
        };
        win.is_on_all_workspaces = () => win.sticky;
        win.is_skip_taskbar = () => win.skipTaskbar;
        win.get_frame_rect = () => Object.assign({}, win.rect);
        win.move_resize_frame = (user, x, y, width, height) => {
            win.resizes.push([user, x, y, width, height]);
            win.rect = {x, y, width, height};
        };
        win.lower = () => {
            win.lowered++;
            const stack = fakeGlobal.display.stack;
            stack.splice(stack.indexOf(win), 1);
            stack.unshift(win);
        };
        win.raise = () => {
            win.raised++;
            const stack = fakeGlobal.display.stack;
            stack.splice(stack.indexOf(win), 1);
            stack.push(win);
        };
        win.get_layer = () => layerOf(win);
        win.is_above = () => win.above;
        win.unmake_above = () => {
            win.above = false;
        };
        win.get_title = () => win.title;
        win.get_user_time = () => win.userTime ?? 0;
        fakeGlobal.display.stack.push(win);
        const actor = {meta_window: win};
        fakeGlobal.actors.push(actor);
        win.actor = actor;
        return win;
    };

    world.map = win => windowManager.emit('map', win.actor);
    return world;
}

module.exports = {makeWorld, EXT_DIR, DATA_DIR, Emitter};
