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
pip install -r requirements.txt       # PyQt6 only
python3 desktop_icon_manager.py       # launch the GUI
```

Requires `PyQt6` (only dependency) plus the system tools `gio`, `xdg-user-dir`,
`pgrep`, and `nemo-desktop` on PATH. Needs an active X11/Wayland session — the
`DISPLAY`/`WAYLAND_DISPLAY` check in `save_current_layout`,
`restore_selected_layout` and `align_current_desktop_to_grid` aborts those
actions with a German warning when neither is set.

No build, test or lint setup — verification is manual (run the GUI on a real
Cinnamon session). Git remote is
`github.com/dev-core-busy/linux-desktop-symbole-ordnen`; commit messages are in
German, matching the rest of the project.

## Deployment layout (important)

The app is designed to be **run from `~/.config/schreibtisch-symbol-manager/`**,
not from this project directory. The bundled `.desktop` launcher and the
in-app "Desktop-Starter erstellen" button both hardcode
`Exec=python3 ~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py`.
This project dir is the source; installation means copying
`desktop_icon_manager.py` and `app_icon.png` to that config path.

Two distinct directories are easy to confuse:
- **Profiles** are written to `~/.config/schreibtisch-symbol-manager/profiles/`
  by `IconManager` at runtime. The `profiles/` folder in this repo is untracked
  local leftover data (gitignored) — the app never reads it.
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
   `configparser` in `_load_keyfile`) natively holds only *special* items
   (home, trash, volumes, network) — **not** regular files. For those special
   items the GKeyFile **is** the authoritative store; GIO does not know them
   (they aren't files). So the app both reads them (`_get_special_items`, saved
   into a profile's `special_items` dict) and writes them back on restore
   (`_restore_special_items`). Regular-file positions must **never** be written
   here (that was the original restore bug) — those go through GIO only.
   - Keyfile **writes must preserve GKeyFile format**: use `_new_keyfile_parser`
     (`configparser` with `interpolation=None` + `optionxform=str`) and
     `write(f, space_around_delimiters=False)` so keys stay `key=value` (no
     spaces) and section-name/key case is preserved. `configparser`'s defaults
     (` = ` delimiter, lowercased keys) would corrupt the file.
   - Only sections already present in the live keyfile are updated on restore;
     an unmounted volume has no section and is skipped. A `.bak` copy is made
     before writing. Each item gets a **fresh per-item
     `nemo-icon-position-timestamp`**, but the global per-monitor
     `nemo-icon-view-layout-timestamp` is **NEVER bumped by the app**. A fresh
     item timestamp alone makes Nemo treat the position as "newer than the last
     layout" and place it exactly. Bumping the layout timestamp yourself forces
     a full grid re-snap under `keep-aligned=true`, which shoves closely-spaced
     icons to new cells via collision resolution — icons land "somewhere". (This
     also means grid snapping is NOT harmless as an earlier note claimed; the
     only safe restore leaves the layout timestamp for Nemo to bump itself.)
   - **Why this matters:** on a desktop dominated by mounted volumes/drives, the
     old code (files only) restored ~nothing visible — every drive icon stayed
     put. Volumes are captured only in *new* profiles; profiles saved before this
     fix have an empty/absent `special_items` and cannot restore drive icons.

**DANGER — do not touch `org.nemo.desktop desktop-layout`.** Its format is
`bool:bool` = *"show desktop folder on primary monitor : show desktop on
remaining monitors"* — **not** keep-aligned/auto-layout. Setting it to
`'false::false'` hides ALL desktop icons. The normal value is `'true::false'`.
(An earlier fix attempt misread this key and blanked the desktop.) Grid
alignment lives elsewhere: `use-desktop-grid` (gsettings) and the per-monitor
`nemo-icon-view-keep-aligned` key in the GKeyFile — the app does not modify
these. With `keep-aligned=true`, Nemo re-snaps and de-collides the whole desktop
whenever it does a full relayout, so restore must avoid triggering one (see the
layout-timestamp rule above): write fresh per-item timestamps only and let Nemo
bump the layout timestamp itself.

**Nemo has its own cell grid and re-snaps onto it.** With `keep-aligned=true`
every position the app writes gets pulled to Nemo's nearest cell. That grid is
not configurable and not exposed by gsettings, so `detect_nemo_grid()` derives
it from the KeyFile: it takes the items carrying the **newest**
`nemo-icon-position-timestamp` (those are the ones Nemo itself placed, hence
exactly on-grid; older entries are leftovers sitting off-grid), then computes
the GCD of the coordinate spacings per axis. On the dev machine that yields
**85×66 starting at (49,16)**. Writing on any other grid (the old hardcoded
100×100 at 50,50) makes evenly spaced icons land on unevenly spaced cells —
that was the "grid alignment isn't symmetric" bug. The GUI seeds the spin boxes
from the detection; `_warn_on_grid_mismatch` logs a warning when the chosen
values aren't a multiple of Nemo's cell size.

**Positions are written only while `nemo-desktop` is stopped.**
`_write_positions` calls `_stop_nemo_desktop` (which polls `pgrep` until the
process is really gone — `-q` returns early), writes GIO + KeyFile, then
`_start_nemo_desktop` in a `finally` so the desktop never stays empty. Writing
into a running nemo-desktop lets it re-snap the fresh values immediately. Both
helpers no-op gracefully when `nemo-desktop` isn't running or installed.

Symlinked launchers on the desktop (including broken ones) still show icons, so
existence checks use `os.path.lexists`, not `os.path.exists`.

### Profiles

JSON files (`<name>.json`) written by `save_profile`, with two icon maps:
- `icons` — regular desktop files keyed by filename, from GIO (KeyFile fallback
  for legacy entries), storing `metadata::nemo-icon-position` and its timestamp.
  Only files that actually have a position are saved.
- `special_items` — keyed by KeyFile section name, plus the read-only `monitor`
  and `icon-scale` values (captured for reference; restore writes back only
  position + timestamp). Sections starting with `desktop-monitor-` are layout
  config, not icons, and are skipped.

`get_profiles` lists them sorted newest-first. `restore_profile` flattens both
maps into one `[(kind, key, x, y)]` list (files and volumes share one surface,
so they must be de-conflicted together), skips icons whose files no longer exist
(`os.path.lexists`), then hands off to `_write_positions`, which computes **one
shared timestamp** for the whole restore and applies it to both the GIO writes
and the KeyFile special items. Volumes with no live KeyFile section are dropped
by `_restore_special_items`.

`_resolve_collisions` runs on every non-aligned restore: Nemo leaves **stale
KeyFile entries**, so a profile can legitimately contain two volumes on the same
position (measured: two such pairs on the dev desktop). Writing both back makes
Nemo place one and shove the other somewhere — the "restore only moves some
icons" bug. Duplicates now deterministically step down by one cell and say so in
the log. Only writes that actually succeed are counted; `_set_icon_position`
returning `False` is logged instead of silently inflating the total.

`get_desktop_files` skips only dotfiles — the app's own
`Schreibtisch-Symbol-Manager.desktop` launcher is deliberately managed like any
other icon, so don't "fix" it by filtering it out.

### Grid alignment

`align_icons_to_grid(grid_x, grid_y, log_callback=None)` aligns the **whole live
desktop**: files (read via `_get_icon_position`, written through GIO) *and*
special items (read/written through the KeyFile). Leaving volumes out was the
second half of the symmetry bug — on the dev desktop 13 of 22 icons are volumes,
so a files-only pass could never look symmetric.

The actual snapping lives in `_snap_layout(entries, grid_x, grid_y, grid)`,
shared with `restore_profile`'s "align on restore" path. It takes a flat
`[(kind, key, x, y)]` list so files and volumes occupy **one** collision map,
rounds via `_snap` and pushes occupied cells down a row. Two details that matter:
- The origin comes from `_grid_origin` — Nemo's detected origin (49,16 here),
  falling back to 50,50. Snapping to a foreign origin guarantees Nemo re-snaps.
- `_snap` uses `floor(x + 0.5)`, not `round()`. Python's `round()` is
  round-half-to-even, so two icons equally far from a cell boundary would snap
  in opposite directions.

## Gotchas

- After editing, the app must be copied to
  `~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py` — that's where
  the `.desktop` launcher runs it from. This repo is just the source.
- The `.desktop` launcher in this repo points `Icon=` at the config-dir
  `app_icon.png`, but the in-app shortcut generator uses the themed icon name
  `preferences-desktop-icons` instead.
