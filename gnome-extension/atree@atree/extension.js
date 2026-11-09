// atree Desktop: keeps the atree widget window pinned to the desktop.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const RESPAWN_SECONDS = 5;
const RECHECK_MS = 1000;
const MAX_TRIES = 5;

// atree's Resume raises the terminal a running talk lives in: on Wayland only the shell can.
const DBUS_PATH = '/org/atree/Desktop';
const DBUS_IFACE = `<node><interface name="org.atree.Desktop">
  <method name="Activate"><arg type="au" direction="in" name="pids"/><arg type="b" direction="out" name="found"/></method>
</interface></node>`;

function nonEmpty(v) {
    return typeof v === 'string' && v.trim() !== '';
}

function nowMs() {
    return GLib.get_monotonic_time() / 1000;
}

export default class AtreeDesktop extends Extension {
    enable() {
        this._config = this._loadConfig();
        this._signals = [];
        this._windowSignals = [];
        this._window = null;
        this._proc = null;
        this._client = null;
        this._wait = null;
        this._respawnId = 0;
        this._checkId = 0;
        this._tries = 0;
        this._gaveUp = false;
        this._pinnedAt = 0;

        this._connect(global.window_manager, 'map', (_wm, actor) => this._onMap(actor), true);
        this._connect(global.display, 'workareas-changed', () => this._check(true));
        this._connect(global.display, 'restacked', () => this._check(false));
        this._connect(Main.layoutManager, 'monitors-changed', () => this._check(true));
        this._export();
        this._spawn();
    }

    disable() {
        if (this._respawnId) {
            GLib.source_remove(this._respawnId);
            this._respawnId = 0;
        }
        for (const [obj, id] of this._signals ?? [])
            obj.disconnect(id);
        this._signals = [];
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._forgetWindow();
        if (this._wait) {
            this._wait.cancel();
            this._wait = null;
        }
        if (this._proc) {
            try {
                this._proc.force_exit();
            } catch (e) {
                console.error(`atree: stopping the widget failed: ${e}`);
            }
            this._proc = null;
        }
        this._client = null;
        this._config = null;
    }

    _connect(obj, name, fn, after = false) {
        const id = after ? obj.connect_after(name, fn) : obj.connect(name, fn);
        this._signals.push([obj, id]);
    }

    _loadConfig() {
        try {
            // written by  atree.py --install-extension , outside the repo
            const file = GLib.build_filenamev([GLib.get_user_data_dir(), 'atree', 'extension.json']);
            const [, bytes] = GLib.file_get_contents(file);
            const cfg = JSON.parse(new TextDecoder().decode(bytes));
            if (cfg && typeof cfg === 'object' && !Array.isArray(cfg) &&
                nonEmpty(cfg.python) && nonEmpty(cfg.app))
                return {python: cfg.python, app: cfg.app};
        } catch (e) {
            // reported below
        }
        console.error('atree: extension.json is missing or invalid; run  atree.py --install-extension  first');
        return {};
    }

    // ------------------------------------------------------------ D-Bus

    _export() {
        try {
            this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_IFACE, {
                Activate: pids => this._activate(pids),
            });
            this._dbus.export(Gio.DBus.session, DBUS_PATH);
        } catch (e) {
            console.error(`atree: exporting ${DBUS_PATH} failed: ${e}`);
            this._dbus = null;
        }
    }

    // Focus a normal window of the first pid that has one (callers list a process and then
    // its ancestors); of several windows, the one used last. Its tab is not chosen.
    _activate(pids) {
        const wins = this._allWindows().filter(w => w && w !== this._window &&
            w.get_window_type() === Meta.WindowType.NORMAL);
        for (const pid of pids) {
            const own = wins.filter(w => Number(w.get_pid()) === Number(pid));
            if (own.length === 0)
                continue;
            const win = own.reduce((a, b) => ((b.get_user_time?.() ?? 0) > (a.get_user_time?.() ?? 0) ? b : a));
            Main.activateWindow(win);
            return true;
        }
        return false;
    }

    // ------------------------------------------------------------ process

    _spawn() {
        const cfg = this._config;
        if (!cfg || !nonEmpty(cfg.python) || !nonEmpty(cfg.app))
            return;
        const argv = [cfg.python, cfg.app, '--widget'];
        try {
            const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
            // GNOME 49+ dropped X11 along with this function: no answer means Wayland
            if (Meta.is_wayland_compositor?.() !== false) {
                if (typeof Meta.WaylandClient.new_subprocess === 'function') {
                    this._client = Meta.WaylandClient.new_subprocess(global.context, launcher, argv);
                    this._proc = this._client.get_subprocess();
                } else {
                    this._client = Meta.WaylandClient.new(global.context, launcher);
                    this._proc = this._client.spawnv(global.display, argv);
                }
            } else {
                this._client = null;
                this._proc = launcher.spawnv(argv);
            }
        } catch (e) {
            console.error(`atree: starting the widget failed: ${e}`);
            this._proc = null;
            this._client = null;
            return;
        }
        const wait = new Gio.Cancellable();
        this._wait = wait;
        this._proc.wait_async(wait, (proc, res) => {
            try {
                proc.wait_finish(res);
            } catch (e) {
                // cancelled or failed: handled below
            }
            if (wait.is_cancelled())
                return;
            this._onExit();
        });
    }

    _onExit() {
        this._forgetWindow();
        this._proc = null;
        this._client = null;
        this._wait = null;
        this._respawnId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, RESPAWN_SECONDS, () => {
            this._respawnId = 0;
            this._spawn();
            return GLib.SOURCE_REMOVE;
        });
    }

    // ------------------------------------------------------------ window

    _owns(win) {
        if (!this._proc)
            return false;
        if (this._client)
            return this._client.owns_window(win);
        return String(win.get_pid()) === String(this._proc.get_identifier());
    }

    _onMap(actor) {
        const win = actor.meta_window ?? actor.get_meta_window?.();
        if (!win || this._window || !this._owns(win))
            return;
        if (win.get_window_type() !== Meta.WindowType.NORMAL || win.get_transient_for())
            return;
        this._track(win);
    }

    _track(win) {
        this._window = win;
        const on = (name, fn) => this._windowSignals.push(win.connect_after(name, fn));
        on('unmanaged', () => this._forgetWindow());
        on('raised', () => this._onRaised());
        for (const name of ['notify::window-type', 'notify::on-all-workspaces', 'notify::skip-taskbar',
            'workspace-changed', 'position-changed', 'size-changed'])
            on(name, () => this._check(false));
        this._tries = 0;
        this._gaveUp = false;
        this._pin();
        this._check(false);
    }

    _forgetWindow() {
        if (this._checkId) {
            GLib.source_remove(this._checkId);
            this._checkId = 0;
        }
        if (this._window) {
            for (const id of this._windowSignals)
                this._window.disconnect(id);
        }
        this._windowSignals = [];
        this._window = null;
    }

    // Desktop Icons NG titles its full-screen windows "@!x,y;FLAGS" and keeps those flagged B
    // at the bottom of the normal layer by lowering them itself.
    _isDing(win) {
        if (win === this._window)
            return false;
        if (win.customJS_ding?.keepAtBottom)
            return true;
        const title = String(win.get_title?.() ?? '');
        return title.startsWith('@!') && (title.split(';')[1] ?? '').includes('B');
    }

    // DING hides its windows from global.get_window_actors() on Wayland, so ask the display.
    _allWindows() {
        return typeof global.display.list_all_windows === 'function'
            ? global.display.list_all_windows()
            : global.get_window_actors().map(a => a.meta_window ?? a.get_meta_window?.());
    }

    _desktopWindows(win) {
        const monitor = win.get_monitor();
        return this._allWindows().filter(w => w && w !== win && w.get_monitor() === monitor &&
            (w.get_window_type() === Meta.WindowType.DESKTOP || this._isDing(w)));
    }

    _pinType(win) {
        // Some DING versions use the normal layer; a desktop window cannot sit above it.
        const normalDing = this._desktopWindows(win).some(w =>
            (w.get_layer?.() ?? Meta.StackLayer.NORMAL) > Meta.StackLayer.DESKTOP);
        return normalDing ? Meta.WindowType.NORMAL : Meta.WindowType.DESKTOP;
    }

    _sink(win) {
        win.lower();
        for (const desktop of this._desktopWindows(win))
            desktop.lower();
    }

    // On shells without a desktop type, or with DING in the normal layer, clicks can raise
    // the widget over apps: sink it again, then keep desktops and DING beneath it.
    _onRaised() {
        const win = this._window;
        if (!win)
            return;
        try {
            this._sink(win);
        } catch (e) {
            console.error(`atree: lowering the widget failed: ${e}`);
        }
    }

    _pin() {
        const win = this._window;
        if (!win)
            return;
        this._pinnedAt = nowMs();
        const steps = [
            () => {
                if (!this._client)
                    return;
                if (typeof win.hide_from_window_list === 'function')
                    win.hide_from_window_list();
                else
                    this._client.hide_from_window_list(win);
            },
            () => {
                if (win.is_above?.())
                    win.unmake_above();
            },
            () => win.set_type?.(this._pinType(win)),
            () => win.stick(),
            () => this._place(),
            () => this._sink(win),
        ];
        for (const step of steps) {
            try {
                step();
            } catch (e) {
                console.error(`atree: pinning step failed: ${e}`);
            }
        }
    }

    _workArea() {
        const index = Main.layoutManager.primaryIndex;
        if (index < 0)
            return null;
        return Main.layoutManager.getWorkAreaForMonitor(index) ?? null;
    }

    _placedOn(area) {
        if (!area || !this._window)
            return false;
        const r = this._window.get_frame_rect();
        return r.x === area.x && r.y === area.y && r.width === area.width && r.height === area.height;
    }

    _place() {
        const area = this._workArea();
        if (!area || !this._window || this._placedOn(area))
            return;
        this._window.move_resize_frame(false, area.x, area.y, area.width, area.height);
    }

    // Still in the layer _pin put it in (something may have lifted it since).
    _inLayer(win) {
        if (win.is_above?.())
            return false;
        if (typeof win.set_type === 'function' && win.get_window_type() !== this._pinType(win))
            return false;
        if (typeof win.get_layer !== 'function')
            return true;
        return win.get_layer() <= Meta.StackLayer.NORMAL;
    }

    _aboveDesktop(win) {
        const rivals = this._desktopWindows(win);
        if (rivals.length === 0)
            return true;
        const stacked = global.display.sort_windows_by_stacking([...rivals, win]);
        return stacked[stacked.length - 1] === win;
    }

    _belowApps(win) {
        const apps = this._allWindows().filter(w => w && w !== win && !this._isDing(w) &&
            [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG].includes(w.get_window_type()) &&
            // a keep-below app sits in a lower layer the widget can't get under: don't chase it
            (w.get_layer?.() ?? Meta.StackLayer.NORMAL) >= Meta.StackLayer.NORMAL);
        const stacked = global.display.sort_windows_by_stacking([win, ...apps]);
        return stacked[0] === win;
    }

    _isPinned() {
        const win = this._window;
        return win.is_on_all_workspaces() &&
            (!this._client || win.is_skip_taskbar()) &&
            this._placedOn(this._workArea()) &&
            this._inLayer(win) &&
            this._aboveDesktop(win) &&
            this._belowApps(win);
    }

    // Coalesced, rate-limited verification; a fresh check forgets earlier failures.
    _check(fresh) {
        if (fresh) {
            this._tries = 0;
            this._gaveUp = false;
        }
        if (this._checkId || !this._window || this._gaveUp)
            return;
        const delay = Math.max(0, Math.ceil(this._pinnedAt + RECHECK_MS - nowMs()));
        this._checkId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._checkId = 0;
            this._verify();
            return GLib.SOURCE_REMOVE;
        });
    }

    _verify() {
        if (!this._window || this._isPinned()) {
            this._tries = 0;
            return;
        }
        if (this._tries < MAX_TRIES) {
            this._tries++;
            this._pin();
            this._check(false);
            return;
        }
        this._gaveUp = true;
        console.warn('atree: the widget does not stay pinned, giving up until the work area changes');
    }
}
