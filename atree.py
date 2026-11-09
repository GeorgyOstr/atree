#!/usr/bin/env python3
"""atree: a desktop tree of coding-agent sessions."""
import ast
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE / ".venv" / "bin" / "python"
EXTENSION = "atree@atree"
ICON = HERE / "atree" / "qt" / "qml" / "icon.svg"
NEEDS_QUOTES = set(" \t\r\n\"'\\><~|&;$*?#()`")

sys.path.insert(0, str(HERE))


def python():
    """The interpreter installed entry points should use."""
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def keyfile_escape(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def exec_arg(s):
    """Quote one argument for a desktop entry's Exec key."""
    s = s.replace("%", "%%")
    if not s or NEEDS_QUOTES & set(s):
        s = '"%s"' % "".join("\\" + c if c in '"`$\\' else c for c in s)
    return keyfile_escape(s)


def install_desktop():
    path = data_home() / "applications" / "atree.desktop"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=atree",
        "Comment=Tree of coding-agent sessions",
        "Exec=%s %s" % (exec_arg(python()), exec_arg(str(HERE / "atree.py"))),
        "Icon=%s" % keyfile_escape(str(ICON)),
        "Terminal=false",
        "Categories=Development;",
        "StartupWMClass=atree",
    ]) + "\n")
    print("installed %s" % path)


def _run(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 127


def _enable_with_gsettings():
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                             capture_output=True, text=True)
        text = out.stdout.strip()
        if out.returncode:
            return False
        if text.startswith("@as "):
            text = text[4:]
        enabled = ast.literal_eval(text)
    except (OSError, ValueError, SyntaxError):
        return False
    if not isinstance(enabled, list):
        return False
    if EXTENSION in enabled:
        return True
    return _run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                 str(enabled + [EXTENSION])]) == 0


def install_extension():
    src = HERE / "gnome-extension" / EXTENSION
    # Machine paths live in atree's data folder, not in the repo, which stays path-free.
    conf = data_home() / "atree" / "extension.json"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(json.dumps({"python": python(), "app": str(HERE / "atree.py")}))
    dst = data_home() / "gnome-shell" / "extensions" / EXTENSION
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or not dst.exists():
        if dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src)
    else:
        sys.exit("%s exists and isn't a link: remove it and run this again" % dst)
    if _run(["gnome-extensions", "enable", EXTENSION]) != 0 and not _enable_with_gsettings():
        print("couldn't enable it: run  gnome-extensions enable %s  after logging in" % EXTENSION)
    print("linked %s -> %s" % (dst, src))
    print("on Wayland, log out and back in once so GNOME Shell loads it")


def install_statusline():
    from atree import statusline
    return statusline.install("%s %s --statusline" % (shlex.quote(python()), shlex.quote(str(HERE / "atree.py"))))


def main():
    args = sys.argv[1:]
    if args == ["--statusline"]:
        from atree import statusline
        sys.exit(statusline.run())
    if "--op" in args:
        from atree.assistant import cli
        sys.exit(cli(args[args.index("--op") + 1:]))
    if "--install-desktop" in args:
        return install_desktop()
    if "--install-extension" in args:
        return install_extension()
    if "--install-statusline" in args:
        sys.exit(install_statusline() or 0)
    if "--uninstall-statusline" in args:
        from atree import statusline
        sys.exit(statusline.uninstall() or 0)
    try:
        import PySide6  # noqa: F401
    except ImportError:
        if VENV_PY.exists() and Path(sys.executable) != VENV_PY:
            os.execv(str(VENV_PY), [str(VENV_PY), str(HERE / "atree.py")] + args)
        sys.exit("PySide6 missing: run  python3 -m venv .venv && .venv/bin/python -m pip install PySide6-Essentials")
    from atree.qt.app import main as gui
    sys.exit(gui(widget="--widget" in args))


if __name__ == "__main__":
    main()
