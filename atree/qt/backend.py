"""The object the Qt Quick view binds to (ui.md §16)."""
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QFont, QFontMetricsF, QGuiApplication

from atree import ops
from atree.agents import AGENTS, BY_NAME
from atree.fsutil import atomic_write
from atree.qt import layout as L
from atree.qt.listmodel import KeyedListModel
from atree.qt.tasks import TaskList

SCAN_EVERY = 4000
USAGE_EVERY = 60000


def collect():
    """One refresh: scanned talks plus archive-index entries, and adapter errors."""
    from atree.scan import scan_all
    errors = []
    talks = scan_all(errors)
    for t in talks:
        t["archived"] = False
    index = ops.archived()
    if not isinstance(index, dict):
        raise ValueError("the archive index is not a JSON object")
    scanned = {t["key"] for t in talks}
    for key, entry in index.items():
        # mid-archive/restore (or after a failed removal) the files are still there: the scan wins
        if isinstance(entry, dict) and key not in scanned:
            talks.append(dict(entry, key=key, archived=True, live=False, live_status=None))
    return talks, errors


def fetch_usage(force=False):
    from atree import usage
    return usage.get(force=force)


def _xdg_data():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def data_dir():
    return os.path.join(_xdg_data(), "atree")


def _guard(fn, *args):
    try:
        return fn(*args)
    except ops.OpError as e:
        return e
    except Exception as e:  # unexpected: show it, hand it on as the result
        traceback.print_exc()
        return e


def _message(e):
    return str(e) or type(e).__name__


class Backend(QObject):
    changed = Signal()
    usageChanged = Signal()
    detailsChanged = Signal()
    tasksChanged = Signal()
    aiChanged = Signal()
    toast = Signal(str, bool)
    undoable = Signal(str, "QVariantList", bool)
    _deliver = Signal(object)

    def __init__(self, parent=None, *, autostart=True, settings_path=None):
        super().__init__(parent)
        self.clock = time.time
        self.mono = time.monotonic
        self.collector = collect
        self.usage_source = fetch_usage
        self.home = os.path.expanduser("~")
        self.settings_path = settings_path or os.path.join(data_dir(), "ui.json")
        self._agent_names = [a.name for a in AGENTS]
        self._askable = set()
        for a in AGENTS:
            try:
                if a.ask_argv():
                    self._askable.add(a.name)
            except Exception:
                pass
        self._metrics = {}
        self._alive = True

        self._filters, self.dirs, self.cards, self._order = L.load_settings(self._read_settings(), self._agent_names)
        self.talks = []
        self._by_key = {}
        self.ranks = L.Ranks()
        self.selection = set()
        self.details_key = ""
        self.held = False
        self.layout_pending = False
        self.shown = None
        self.drop_path = ""
        self._visible = []
        self._stats = "0 talks · 0 running · 0 total"
        self._activity = L.activity_grid([], [], self._today())
        self._agents = []
        self._details = {}
        self._announced = None
        self._seen_errors = set()
        self._ai = self._ai_idle()
        self._ai_keys = set()
        self._ai_gen = 0
        self._usage_raw = {}
        self._usage = []

        self._scanning = False
        self._scan_queued = False
        self._after_op = False
        self._scan_minute = None
        self._whoami = L.identity()

        self._nodes = KeyedListModel(list(L.ROLE_DEFAULTS), self)
        self._rows = {}
        self._deliver.connect(self._on_deliver, Qt.QueuedConnection)
        self._pool = ThreadPoolExecutor(2, thread_name_prefix="atree-scan")
        self._ops = ThreadPoolExecutor(1, thread_name_prefix="atree-ops")
        self._tasks = TaskList(self.tasksChanged.emit, lambda m: self.toast.emit(m, True))

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_EVERY)
        self._scan_timer.timeout.connect(self._tick)
        self._usage_timer = QTimer(self)
        self._usage_timer.setInterval(USAGE_EVERY)
        self._usage_timer.timeout.connect(lambda: self._fetch_usage(False))
        self._rebuild()
        if autostart:
            self._scan_timer.start()
            self._usage_timer.start()
            self.refresh()
            self._fetch_usage(False)

    # ------------------------------------------------------------ plumbing

    def _today(self):
        import datetime
        return datetime.date.fromtimestamp(self.clock())

    def measure(self, text, px=13, bold=True):
        fm = self._metrics.get((px, bold))
        if fm is None:
            f = QFont()
            f.setPixelSize(px)
            f.setBold(bold)
            fm = self._metrics[(px, bold)] = QFontMetricsF(f)
        return fm.horizontalAdvance(text)

    def _post(self, callback, value):
        try:
            self._deliver.emit((callback, value))
        except RuntimeError:
            pass  # the backend is gone

    def _on_deliver(self, item):
        if self._alive:
            callback, value = item
            callback(value)

    def _submit(self, pool, fn, done, *args):
        try:
            fut = pool.submit(_guard, fn, *args)
        except RuntimeError:
            return None  # shut down
        fut.add_done_callback(lambda f: None if f.cancelled() else self._post(done, f.result()))
        return fut

    def _spawn(self, fn, done, *args):
        threading.Thread(target=lambda: self._post(done, _guard(fn, *args)), daemon=True).start()

    @Slot()
    def shutdown(self):
        self._alive = False
        self._scan_timer.stop()
        self._usage_timer.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._ops.shutdown(wait=False)

    # ------------------------------------------------------------ settings

    # ui.json is read once, at start-up; app and widget each keep their own state in memory
    # and the last writer wins

    def _read_settings(self):
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save_settings(self):
        try:
            atomic_write(self.settings_path, json.dumps(
                L.settings_payload(self._filters, self.dirs, self.cards, self._order), indent=1))
        except OSError as e:
            self.toast.emit("can't save settings: %s" % e, True)

    # ------------------------------------------------------------ scanning

    def _tick(self):
        self.refresh()

    @Slot()
    def refresh(self):
        self._tasks.reload()
        if self._scanning:
            self._scan_queued = True
            return
        self._scanning = True
        if self._submit(self._pool, self.collector, self._scan_result) is None:
            self._scanning = False

    def _scan_result(self, result):
        if isinstance(result, Exception):
            self.apply_scan(None, (), failure=_message(result))
        else:
            self.apply_scan(*result)

    def apply_scan(self, talks, errors=(), failure=None):
        """Scan completion (also the seam tests feed synthetic results through)."""
        self._scanning = False
        for e in errors:
            self._error_once(e)
        if failure is not None:
            self._error_once("scan failed: %s" % failure)
        else:
            talks = list(talks)
            minute = int(self.clock() // 60)  # age labels and the age filter have minute resolution
            before = dict(self.ranks.as_dict())
            same = talks == self.talks and minute == self._scan_minute and not self._after_op
            self._scan_minute = minute
            if not same:
                self.talks = talks
                self._by_key = {t["key"]: t for t in self.talks}
                self._agents = self._agents_here()
                self._activity = self._compute_activity()
            self.ranks.update(self.talks, self.mono())
            if not same or self.ranks.as_dict() != before:
                self._rebuild(passive=not self._after_op)
        if self._scan_queued:
            self._scan_queued = False
            self.refresh()
        elif failure is None:
            self._after_op = False

    def _error_once(self, msg):
        if msg not in self._seen_errors:
            self._seen_errors.add(msg)
            self.toast.emit(msg, True)

    def _enabled(self, name):
        return self._filters.get(name, True) is not False

    def _agents_here(self):
        counts = {}
        for t in self.talks:
            counts[t.get("tool")] = counts.get(t.get("tool"), 0) + 1
        return [{"name": a.name, "label": a.label, "color": a.color, "count": counts[a.name]}
                for a in self._ordered() if a.name in counts]

    def _ordered(self):
        """AGENTS in the user's order (drag a filter chip or usage block to change it)."""
        return [BY_NAME[n] for n in self._order]

    def _compute_activity(self):
        talks = [t for t in self.talks if self._enabled(t.get("tool"))]
        return L.activity_grid(talks, [(a.name, a.label) for a in self._ordered()], self._today())

    # ------------------------------------------------------------ layout

    def _ctx(self):
        return L.Ctx(self._filters, self.clock(), self.home, self.dirs, self.cards,
                     self.ranks.as_dict(), self.selection, self._ai_keys, self.measure)

    def _rebuild(self, passive=False, reset=False):
        if reset:
            self.ranks.reset(self.talks, self.mono())
        ctx = self._ctx()
        vis = L.visible_talks(self.talks, self._filters, ctx.now, self._ai_keys)
        self._visible = vis
        exact = L.compute_layout(vis, ctx)
        if passive and self.held and self.shown is not None:
            self.shown = L.restyle(self.shown, {t["key"]: t for t in vis}, ctx)
            self.layout_pending = exact.shape != self.shown.shape
        else:
            self.shown = exact
            self.layout_pending = False
        self._sync_models()
        running = sum(1 for t in vis if t.get("live"))
        self._stats = "%d talks · %d running · %d total" % (len(vis), running, len(self.talks))
        self._refresh_details()
        self._announce()

    def _sync_models(self):
        items = []
        for n in self.shown.items():
            n = dict(n)
            n["drop"] = bool(self.drop_path) and n["kind"] != "link" and n["path"] == self.drop_path
            items.append(n)
        self._nodes.sync(items)
        for path, model in self._rows.items():
            model.sync(self.shown.rows.get(path, []))

    def _announce(self):
        state = (self._stats, self.shown.width, self.shown.height,
                 json.dumps(self._filters, sort_keys=True), len(self.selection),
                 json.dumps(self._activity, sort_keys=True), json.dumps(self._agents),
                 json.dumps(self._search_agent()), json.dumps(self._talk_agent()), self.layout_pending)
        if state != self._announced:
            self._announced = state
            self.changed.emit()

    def _leftmost(self, ok):
        """The leftmost agent chip passing ``ok`` → {name, label}, or {} when none does."""
        for a in self._agents:
            if ok(a["name"]):
                return {"name": a["name"], "label": a["label"]}
        return {}

    def _search_agent(self):
        """Who Enter in the search field asks: the leftmost shown agent that can search."""
        return self._leftmost(lambda n: self._enabled(n) and n in self._askable)

    def _talk_agent(self):
        """Who a folder's play button starts: the leftmost agent with a new-talk command."""
        return self._leftmost(lambda n: bool(BY_NAME[n].new_command))

    # ------------------------------------------------------------ properties

    def _get_nodes(self):
        return self._nodes

    nodeModel = Property(QObject, _get_nodes, constant=True)
    stats = Property(str, lambda self: self._stats, notify=changed)
    contentWidth = Property(float, lambda self: float(self.shown.width), notify=changed)
    contentHeight = Property(float, lambda self: float(self.shown.height), notify=changed)
    layoutPending = Property(bool, lambda self: self.layout_pending, notify=changed)
    filters = Property("QVariantMap", lambda self: dict(self._filters), notify=changed)
    selectedCount = Property(int, lambda self: len(self.selection), notify=changed)
    activity = Property("QVariantMap", lambda self: self._activity, notify=changed)
    agents = Property("QVariantList", lambda self: self._agents, notify=changed)
    searchAgent = Property("QVariantMap", lambda self: self._search_agent(), notify=changed)
    talkAgent = Property("QVariantMap", lambda self: self._talk_agent(), notify=changed)
    ai = Property("QVariantMap", lambda self: dict(self._ai), notify=aiChanged)
    whoami = Property("QVariantMap", lambda self: self._whoami, constant=True)
    agentInfo = Property("QVariantMap", lambda self: {a.name: {"label": a.label, "color": a.color}
                                                      for a in AGENTS}, constant=True)
    usage = Property("QVariantList", lambda self: self._usage, notify=usageChanged)
    details = Property("QVariantMap", lambda self: self._details, notify=detailsChanged)
    tasks = Property("QVariantList", lambda self: self._tasks.items, notify=tasksChanged)

    @Slot(str, result=QObject)
    def cardModel(self, path):
        model = self._rows.get(path)
        if model is None:
            model = self._rows[path] = KeyedListModel(L.ROW_ROLES, self)
            model.sync(self.shown.rows.get(path, []))
        return model

    # ------------------------------------------------------------ filters

    @Slot(str, "QVariant")
    def setFilter(self, name, value):
        if name not in self._filters:
            return
        cur = self._filters[name]
        if isinstance(cur, bool):
            value = bool(value)
        elif name == "age":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
            if value < 0:
                return
        elif name == "sort" and value not in L.SORTS:
            return
        else:
            value = "" if value is None else str(value)
        if value == cur:
            return
        self._filters[name] = value
        if name in self._agent_names:
            self._agent_toggled(name, value)
        if name != "q":
            self._save_settings()
        self._rebuild(reset=True)

    def _agent_toggled(self, name, on):
        self._activity = self._compute_activity()
        self._update_usage()
        if on:
            return
        results = [r for r in self._ai["results"] if r.get("tool") != name]
        if len(results) != len(self._ai["results"]):
            self._ai = dict(self._ai, results=results)
            self._ai_keys = {r["key"] for r in results}
            self.aiChanged.emit()

    @Slot("QVariantList")
    def reorderAgents(self, names):
        """``names``: some agents in their new order; they swap among the slots they held."""
        order = L.reorder_subset(self._order, [str(n) for n in names])
        if order == self._order:
            return
        self._order = order
        self._agents = self._agents_here()
        self._activity = self._compute_activity()
        self._save_settings()
        self._update_usage()
        self._announce()

    @Slot(bool)
    def holdLayout(self, held):
        held = bool(held)
        if held == self.held:
            return
        self.held = held
        if not held and self.layout_pending:
            self._rebuild(passive=True)

    @Slot()
    def applyLayout(self):
        self._rebuild(reset=True)

    @Slot(result=bool)
    def orderIsStale(self):
        """Recent order held by the stable view differs from the talks' real recency."""
        if self._filters.get("sort") != "recent" or self.ranks.ranks is None:
            return False
        held = sorted(self._visible, key=lambda t: (self.ranks.get(t["key"], L.natural_recent(t)), t["key"]))
        real = sorted(self._visible, key=lambda t: (L.natural_recent(t), t["key"]))
        return [t["key"] for t in held] != [t["key"] for t in real]

    # ------------------------------------------------------------ folding

    def _shown_node(self, uid):
        return next((n for n in self.shown.nodes if n["uid"] == uid), None)

    @Slot(str)
    def toggleDir(self, path):
        node = self._shown_node("dir:" + path)
        folded = node["collapsed"] if node else bool(self.dirs.get(path, False))
        self.dirs[path] = not folded
        self._save_settings()
        self._rebuild()

    @Slot(str, str)
    def setCardMode(self, path, mode):
        if mode not in L.CARD_MODES:
            return
        self.cards[path] = mode
        self._save_settings()
        self._rebuild()

    @Slot(str, bool)
    def setAllUnder(self, path, folded):
        dirs, cards = L.all_tree(self._visible, self._ctx())
        top = dirs[0] if dirs else None  # the tree's top node stays open, whatever its path
        for d in dirs:
            if d != path and d != top and L.under(d, path):
                self.dirs[d] = bool(folded)
        for c in cards:
            if L.under(c, path):
                self.cards[c] = "collapsed" if folded else "compact"
        self._save_settings()
        self._rebuild()

    @Slot()
    def autoFold(self):
        self.dirs.clear()
        self.cards.clear()
        self._save_settings()
        self._rebuild()

    # ------------------------------------------------------------ selection

    @Slot(str, bool)
    def select(self, key, additive=False):
        if additive:
            self.selection ^= {key}
        else:
            self.selection = {key}
        if key in self.selection:
            self.details_key = key
        elif self.details_key not in self.selection:
            self.details_key = min(self.selection) if self.selection else ""
        self._rebuild(passive=True)

    @Slot(str)
    def selectFolder(self, path):
        self.selection = {t["key"] for t in self._visible
                          if not t.get("archived") and L.under(t.get("cwd") or "", path)}
        if self.details_key not in self.selection:
            self.details_key = min(self.selection) if self.selection else ""
        self._rebuild(passive=True)

    @Slot()
    def clearSelection(self):
        if not self.selection and not self.details_key:
            return
        self.selection = set()
        self.details_key = ""
        self._rebuild(passive=True)

    @Slot(str, result="QVariantList")
    def actionKeys(self, key):
        return sorted(self.selection) if key in self.selection else [key]

    @Slot("QVariant", result="QVariantList")
    def describe(self, keys):
        if isinstance(keys, str):
            try:
                keys = json.loads(keys)
            except ValueError:
                keys = [keys]
        out = []
        for k in keys or []:
            t = self._by_key.get(k)
            if t:
                out.append({"key": k, "title": L.collapse(t.get("title")) or k,
                            "cwd": t.get("cwd") or "", "folder": self.tildify(t.get("cwd") or ""),
                            "live": bool(t.get("live")), "archived": bool(t.get("archived"))})
        return out

    def _refresh_details(self):
        t = self._by_key.get(self.details_key) if self.details_key else None
        d = self._describe_talk(t) if t else {}
        if d != self._details:
            self._details = d
            self.detailsChanged.emit()

    def _describe_talk(self, t):
        now = self.clock()

        def stamp(v):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "—"

        upd = t.get("updated") or 0
        if t.get("agents"):
            subs = str(t["agents"])
        elif t.get("parent"):
            subs = "is a subagent"
        else:
            subs = "—"
        rows = [
            "folder", self.tildify(t.get("cwd") or ""),
            "started", stamp(t.get("created")),
            "last active", "%s  (%s ago)" % (stamp(upd), L.age_text(now - upd)) if upd else "—",
            "prompts", str(t["prompts"]) if t.get("prompts") else "—",
            "context", "%s tokens" % L.fmt_tokens(t["context"]) if t.get("context") else "—",
            "tokens used", L.fmt_tokens(t["tokens"]) if t.get("tokens") else "—",
            "subagents", subs,
            "branch", t.get("branch") or "—",
            "size", "%.1f MB" % ((t.get("size") or 0) / 1e6),
            "id", str(t.get("id") or ""),
            "file", (t.get("archive") if t.get("archived") else t.get("path")) or "",
        ]
        return {"key": t["key"], "title": L.collapse(t.get("title")) or t["key"],
                "tool": t.get("tool", ""), "live": bool(t.get("live")),
                "status": t.get("live_status") or "", "archived": bool(t.get("archived")),
                "prompt": t.get("first_msg") or "", "rows": rows,
                "selected": len(self.selection)}

    # ------------------------------------------------------------ drag and drop

    @Slot(float, float, result=str)
    def dropAt(self, x, y):
        path = L.drop_target(self.shown, x, y)
        if path != self.drop_path:
            old, self.drop_path = self.drop_path, path
            for n in self.shown.nodes:
                if n["path"] in (old, path) and n["path"]:
                    self._nodes.set_role(n["uid"], "drop", n["path"] == path)
        return path

    # ------------------------------------------------------------ operations

    def _batch(self, fn, keys, label, undoable=False):
        keys = list(keys)

        def job():
            done, errors = [], []
            for k in keys:
                try:
                    fn(k)
                    done.append(k)
                except Exception as e:
                    if not isinstance(e, ops.OpError):
                        traceback.print_exc()
                    errors.append(_message(e))
            return done, errors

        self._submit(self._ops, job, lambda r: self.batch_done(r, label, undoable))

    def batch_done(self, result, label, undoable=False):
        done, errors = ([], [_message(result)]) if isinstance(result, Exception) else result
        if undoable and done:
            self.undoable.emit("\n".join([label % len(done)] + list(errors)), list(done), bool(errors))
        elif errors:
            self.toast.emit("\n".join(errors), True)
        else:
            self.toast.emit(label % len(done), False)
        self.selection = set()
        self.details_key = ""
        self._rebuild()
        if done:
            self._after_op = True
        self.refresh()

    def _talks(self, keys):
        return [self._by_key[k] for k in keys or [] if k in self._by_key]

    @Slot("QVariantList", str)
    def move(self, keys, target):
        keys = [t["key"] for t in self._talks(keys) if not t.get("archived")]
        if keys:
            dest = L.expand(target)
            self._batch(lambda k: ops.move(k, dest), keys, "moved %d talk(s)")

    @Slot("QVariantList")
    def archive(self, keys):
        keys = [t["key"] for t in self._talks(keys) if not t.get("archived") and not t.get("live")]
        if not keys:
            self.toast.emit("running talks can't be archived", True)
            return
        self._batch(ops.archive, keys, "archived %d talk(s)", undoable=True)

    @Slot("QVariantList")
    def restore(self, keys):
        keys = list(keys or [])
        if keys:
            self._batch(ops.restore, keys, "restored %d talk(s)")

    @Slot("QVariantList")
    def deleteArchived(self, keys):
        keys = [t["key"] for t in self._talks(keys) if t.get("archived")]
        if not keys:
            self.toast.emit("archive a talk before deleting it", True)
            return
        self._batch(ops.delete_archived, keys, "deleted %d talk(s)")

    def _report(self, ok_text):
        def done(result):
            if isinstance(result, Exception):
                self.toast.emit(_message(result), True)
            else:
                self.toast.emit(ok_text if isinstance(ok_text, str) else ok_text(result), False)
        return done

    @Slot(str)
    def resume(self, key):
        self._spawn(ops.open_terminal, self._report(str), key)

    @Slot(str)
    def newTalk(self, path):
        a = BY_NAME.get(self._talk_agent().get("name"))
        if a is None:
            return
        self._spawn(ops.run_in_terminal, self._report("opening %s…" % a.new_command), path, a.new_command)

    @Slot(str)
    def copyResume(self, key):
        def job():
            cwd, cmd = ops.resume_command(key)
            return "cd %s && %s" % (shlex.quote(cwd), cmd)

        def done(result):
            if isinstance(result, Exception):
                self.toast.emit(_message(result), True)
                return
            self._clip(result)
            self.toast.emit("copied: " + result, False)

        self._spawn(job, done)

    def _clip(self, text):
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    @Slot(str)
    def copyText(self, text):
        self._clip(text)
        self.toast.emit("copied", False)

    @Slot(str)
    def openFolder(self, path):
        exe = shutil.which("xdg-open")
        if exe and os.path.isdir(path):
            subprocess.Popen([exe, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)

    @Slot(str)
    def openAssistant(self, agent):
        a = BY_NAME.get(agent)
        if a is None or not a.new_command:
            return
        from atree import assistant
        self._spawn(assistant.open_terminal, self._report("opening %s in the assistant folder…" % a.label),
                    agent, list(self.talks))

    # ------------------------------------------------------------ AI search

    @staticmethod
    def _ai_idle():
        return {"busy": False, "agent": "", "query": "", "results": [], "error": ""}

    @Slot(str, str)
    def aiSearch(self, agent, query):
        a = BY_NAME.get(agent)
        if a is None or not str(query).strip() or self._ai["busy"]:
            return
        self._ai_gen += 1
        gen = self._ai_gen
        self._ai = {"busy": True, "agent": a.label, "query": query.strip(), "results": [], "error": ""}
        self.aiChanged.emit()
        from atree import aisearch
        sessions = [t for t in self.talks if self._enabled(t.get("tool"))]
        self._spawn(aisearch.search, lambda r: self.ai_answer(gen, r), agent, query, sessions)

    def ai_answer(self, gen, result):
        if gen != self._ai_gen:
            return
        if isinstance(result, Exception):
            self._ai = dict(self._ai, busy=False, results=[], error=_message(result))
            had = bool(self._ai_keys)
            self._ai_keys = set()
            self.aiChanged.emit()
            self._rebuild(reset=had)
            return
        results = []
        for r in result:
            t = self._by_key.get(r.get("key"))
            if t and self._enabled(t.get("tool")):
                results.append({"key": t["key"], "why": str(r.get("why") or ""),
                                "title": L.collapse(t.get("title")) or t["key"],
                                "folder": self.tildify(t.get("cwd") or ""), "tool": t.get("tool", "")})
        self._ai = dict(self._ai, busy=False, results=results, error="" if results else "nothing fits")
        self._ai_keys = {r["key"] for r in results}
        self.aiChanged.emit()
        self._rebuild(reset=True)

    @Slot()
    def clearAi(self):
        a = self._ai
        if not (a["busy"] or self._ai_keys or a["results"] or a["error"]):
            return
        self._ai_gen += 1
        had = bool(self._ai_keys)
        self._ai = self._ai_idle()
        self._ai_keys = set()
        self.aiChanged.emit()
        if had:
            self._rebuild(reset=True)

    # ------------------------------------------------------------ usage

    @Slot()
    def refreshUsage(self):
        self._fetch_usage(True)

    def _fetch_usage(self, force):
        self._submit(self._pool, self.usage_source, self._usage_result, force)

    def _usage_result(self, result):
        if isinstance(result, Exception) or not isinstance(result, dict):
            return
        self.apply_usage(result)

    def apply_usage(self, data):
        self._usage_raw = dict(data)
        self._update_usage()

    def _update_usage(self):
        groups = [g for g in L.usage_groups(self._usage_raw, [(a.name, a.label) for a in self._ordered()],
                                            self.clock())
                  if self._enabled(g["key"])]
        if groups != self._usage:
            self._usage = groups
            self.usageChanged.emit()

    # ------------------------------------------------------------ tasks

    @Slot(str)
    def addTask(self, text):
        self._tasks.add(text)

    @Slot(str, bool)
    def setTaskDone(self, tid, done):
        self._tasks.set_done(tid, done)

    @Slot(str, str)
    def editTask(self, tid, text):
        self._tasks.edit(tid, text)

    @Slot(str)
    def removeTask(self, tid):
        self._tasks.remove(tid)

    @Slot(str, int)
    def moveTask(self, tid, to):
        self._tasks.move(tid, to)

    @Slot()
    def clearDoneTasks(self):
        self._tasks.clear_done()

    # ------------------------------------------------------------ paths

    @Slot(str, result=str)
    def tildify(self, path):
        return L.tildify(path or "", self.home)

    @Slot(str, result=str)
    def expand(self, path):
        return L.expand(path)

    @Slot(str, result=bool)
    def isDir(self, path):
        return L.is_dir(path)

    @Slot(str, result=QUrl)
    def localFileUrl(self, path):
        return QUrl.fromLocalFile(L.expand(path))

    @Slot("QVariant", result=str)
    def localFilePath(self, url):
        if not isinstance(url, QUrl):
            url = QUrl(str(url))
        return url.toLocalFile()
