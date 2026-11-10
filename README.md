# atree

A desktop map of your coding-agent sessions — Claude Code, Codex, Gemini CLI,
OpenCode and GitHub Copilot CLI — grouped by the folder each one was started in.
Python backend, Qt Quick (QML) front end.

    python3 -m venv .venv && .venv/bin/python -m pip install PySide6-Essentials
    ./atree.py                       # open the window
    ./atree.py --install-desktop     # add "atree" to the applications menu
    ./atree.py --install-statusline  # let Claude Code report its plan limits to atree
    ./atree.py --install-extension   # GNOME: show atree as a live desktop widget

## Requirements

Linux with GNOME, on Wayland or X11. Python 3.10 or newer (3.12+ recommended: restoring an
archive relies on tarfile's data filter; 3.13+ keeps alarms exact across suspend) and
PySide6-Essentials 6.8 or newer. Used when present: `gsettings` (your default terminal),
`notify-send` and `canberra-gtk-play` or `paplay` (alarms), `xdg-open` (open a folder) and
Desktop Icons NG (the widget leaves its icons clickable). The widget needs GNOME Shell 45–51.

## What it does

- **Tree** of folders with one card of talks each; cards fold, show a compact list or
  the full list. Running talks get a green dot.
- **Resume** a talk in your default terminal; a running one is raised in its terminal window
  instead (needs the GNOME extension on Wayland) or attached from tmux or screen, **move** Claude and Codex talks to another
  folder (their transcripts are rewritten, with a backup first), **archive** talks into
  verified `.tar.gz` files (undo for a few seconds; restore or delete later).
- **Search** by text as you type; press Enter to let the leftmost agent that can (Claude,
  Codex or Gemini) rank the talks that fit the idea, headless. Drag the agent chips or usage
  blocks sideways to choose who comes first; a folder's ▶ starts a new talk with it.
- **Assistant**: opens Claude or Codex in atree's own folder with a snapshot of the tree,
  to talk through cleanup; it acts only through `atree.py --op ...`.
- **Usage bars** for Claude's 5-hour and weekly limits and Codex's limits.
- **Timers** (`25m tea`, `1h30`, `14:30 call`) and a **calendar** fed by iCal addresses,
  with reminders.

## Desktop widget (GNOME)

`./atree.py --install-extension` links `gnome-extension/atree@atree` into GNOME Shell and enables
it. The extension starts `atree.py --widget`, a frameless see-through window, and keeps it on
the desktop: on every workspace, out of the dock and Alt+Tab, under your windows but above
Desktop Icons NG, sized to the primary work area. The block where Desktop Icons NG puts its
icons stays click-through, and atree keeps its own controls out of it. On Wayland, log out and
back in once so GNOME Shell loads the extension (also after updating it). After moving the
folder or updating from a version that kept `config.json` in the repo, run
`./atree.py --install-extension` again. Turn it off with `gnome-extensions disable atree@atree`.

## Files

atree keeps its own data in `$XDG_DATA_HOME/atree` (archive, backups, assistant folder, widget launch paths,
timers, calendars, tasks, UI state) and `$XDG_CACHE_HOME/atree`. Sessions are read where
each CLI keeps them: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_CLI_HOME`, `COPILOT_HOME`
and `OPENCODE_DB` are honoured.

## Command line for scripts and the assistant

    ./atree.py --op archive|restore|delete KEY...
    ./atree.py --op move FOLDER KEY...
    ./atree.py --op task "TEXT"
    ./atree.py --op tree

## Tests

    .venv/bin/python -m unittest discover -s tests       # Python and offscreen Qt
    node --test tests/test_gnome_*.js                     # the GNOME extension
    gjs tests/gjs_node_shim.js "$PWD"/tests/test_gnome_*.js   # same, without Node
