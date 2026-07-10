# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file PyQt6 desktop GUI ("Schreibtisch Symbol-Manager") for saving and
restoring the positions of icons on a Linux **Cinnamon/Nemo** desktop. Users
capture the current icon arrangement into named JSON profiles and later restore
them, optionally snapping icons to a grid. UI text and code comments are in
**German** — keep new user-facing strings and log messages in German to match.

## Running

```bash
python3 desktop_icon_manager.py       # launch the GUI
```

Requires `PyQt6` (only dependency) plus the system tools `gio`, `xdg-user-dir`,
`pgrep`, and `nemo-desktop` on PATH. Needs an active X11/Wayland session — the
app detects a missing `DISPLAY`/`WAYLAND_DISPLAY` and disables position
save/restore/align actions with a warning.

There is no build, test, or lint setup and no git repository.

## Deployment layout (important)

The app is designed to be **run from `~/.config/schreibtisch-symbol-manager/`**,
not from this project directory. The bundled `.desktop` launcher and the
in-app "Desktop-Starter erstellen" button both hardcode
`Exec=python3 ~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py`.
This project dir is the source; installation means copying
`desktop_icon_manager.py` and `app_icon.png` to that config path.

Two distinct directories are easy to confuse:
- **Profiles** are written to `~/.config/schreibtisch-symbol-manager/profiles/`
  by `IconManager` at runtime. The `profiles/` folder in this repo is sample data.
- **Nemo metadata** (the file Cinnamon actually reads) lives at
  `~/.config/nemo/desktop-metadata`.

## Architecture

The whole program is `desktop_icon_manager.py`, split into two classes:

- **`IconManager`** — all backend logic, no Qt. Reads/writes icon positions and
  manages profiles.
- **`DesktopIconManagerApp(QMainWindow)`** — the GUI. Left panel = profile list
  + save button; right panel = details, grid options, activity log, and
  restore/delete/shortcut actions. Uses a hardcoded Catppuccin Mocha stylesheet
  (`DARK_STYLE`). Long-running actions accept a `log_callback` so backend
  progress streams into the log widget.

### The key mechanism: how icon positions are persisted

This is the crux of the codebase, and getting it wrong silently breaks restore
(icons don't move). Verified behavior of this Cinnamon/Nemo setup:

1. **GIO metadata is the authoritative store for regular desktop files.** Nemo
   reads/writes each file's `metadata::nemo-icon-position` xattr via GIO
   (`gio info`/`gio set`, backed by `~/.local/share/gvfs-metadata/`).
   `_set_icon_position` writes here; `get_gio_metadata`/`_get_icon_position`
   read here first. **Writes must use a current timestamp** so Nemo treats the
   position as newest and doesn't discard it during a relayout.
2. **The Nemo GKeyFile** at `~/.config/nemo/desktop-metadata` (read via
   `configparser` in `_load_keyfile`/`_get_icon_position`) natively holds only
   *special* items (home, trash, volumes, network) — **not** regular files.
   It's kept only as a read fallback for old profiles; the app no longer writes
   it. (Writing regular-file positions here was the original restore bug.)

**DANGER — do not touch `org.nemo.desktop desktop-layout`.** Its format is
`bool:bool` = *"show desktop folder on primary monitor : show desktop on
remaining monitors"* — **not** keep-aligned/auto-layout. Setting it to
`'false::false'` hides ALL desktop icons. The normal value is `'true::false'`.
(An earlier fix attempt misread this key and blanked the desktop.) Grid
alignment lives elsewhere: `use-desktop-grid` (gsettings) and the per-monitor
`nemo-icon-view-keep-aligned` key in the GKeyFile — the app does not modify
these; profile coordinates are already grid-aligned so Nemo's grid snapping is
harmless.

**Applying changes requires restarting the desktop shell.** After writing
positions, `_restart_nemo_desktop` runs `nemo-desktop -q` then relaunches it
(GIO metadata is read at startup). It gracefully no-ops if `nemo-desktop` isn't
running or installed.

Symlinked launchers on the desktop (including broken ones) still show icons, so
existence checks use `os.path.lexists`, not `os.path.exists`.

### Profiles

JSON files (`<name>.json`) written by `save_profile`, one dict per icon under
`icons`, keyed by filename, storing `metadata::nemo-icon-position` and its
timestamp. `get_profiles` lists them sorted newest-first; `restore_profile`
applies one (skipping icons whose files no longer exist).

### Grid alignment

`align_icons_to_grid(grid_x, grid_y, custom_icons=None)` has two modes:
- `custom_icons=None` → aligns the live desktop and writes back to the KeyFile.
- `custom_icons=<dict>` → aligns a profile's icon dict in-memory and returns it
  (used by `restore_profile` when "align on restore" is checked).
It rounds each position to the nearest grid point with a fixed 50px offset and
resolves collisions by pushing occupied cells down a row.

## Gotchas

- After editing, the app must be copied to
  `~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py` — that's where
  the `.desktop` launcher runs it from. This repo is just the source.
- The `.desktop` launcher in this repo points `Icon=` at the config-dir
  `app_icon.png`, but the in-app shortcut generator uses the themed icon name
  `preferences-desktop-icons` instead.
