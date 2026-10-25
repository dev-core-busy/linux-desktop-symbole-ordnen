#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__version__ = "1.0"

import os
import sys
import json
import subprocess
import datetime
import time
import configparser
import shutil
import math
import re
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QLabel, QLineEdit, QMessageBox,
    QInputDialog, QFrame, QListWidgetItem, QTextEdit, QSplitter,
    QSpinBox, QCheckBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QIcon

# Backend-Klasse zur Steuerung der Desktop-Symbole
class IconManager:
    def __init__(self):
        # Profilverzeichnis definieren und erstellen
        self.config_dir = os.path.expanduser("~/.config/schreibtisch-symbol-manager")
        self.profile_dir = os.path.join(self.config_dir, "profiles")
        os.makedirs(self.profile_dir, exist_ok=True)

        # Desktop-Verzeichnis dynamisch ermitteln (xdg-user-dir DESKTOP)
        try:
            self.desktop_dir = subprocess.check_output(['xdg-user-dir', 'DESKTOP']).decode('utf-8').strip()
        except Exception:
            self.desktop_dir = os.path.expanduser("~/Schreibtisch")
            if not os.path.exists(self.desktop_dir):
                self.desktop_dir = os.path.expanduser("~/Desktop")

        # GKeyFile für Nemo-Desktop-Metadaten. Für Spezial-Objekte (Volumes,
        # home, trash, network) ist DAS der maßgebliche Store, den Cinnamon liest.
        self.nemo_metadata_path = os.path.expanduser("~/.config/nemo/desktop-metadata")
        self._keyfile = self._new_keyfile_parser()
        self._load_keyfile()

    @staticmethod
    def _new_keyfile_parser():
        """Erzeugt einen ConfigParser, der zum GKeyFile-Format von Nemo passt.

        - interpolation=None: '%' in Werten wird nicht als Interpolation gedeutet.
        - optionxform=str: Schlüssel-Groß/Kleinschreibung bleibt exakt erhalten.
        Das ist wichtig, weil wir das Keyfile nicht nur lesen, sondern für
        Spezial-Objekte auch WIEDER SCHREIBEN (format-erhaltend).
        """
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        return cp

    # ---------- GKeyFile (nur Lese-Fallback) ----------
    #
    # WICHTIG: Für reguläre Dateien auf dem Desktop liest Nemo/Cinnamon die
    # Icon-Positionen NICHT aus diesem GKeyFile, sondern aus den GIO-Metadaten
    # (gvfs) der jeweiligen Datei. Das GKeyFile enthält nativ nur Spezialobjekte
    # (home, trash, Volumes, network). Wir schreiben Positionen deshalb via GIO
    # (siehe _set_icon_position) und nutzen das KeyFile nur noch als Lese-Fallback
    # für Alt-Profile.

    def _load_keyfile(self):
        """Lädt die Nemo-Desktop-Metadaten frisch aus dem GKeyFile neu ein."""
        self._keyfile = self._new_keyfile_parser()
        if os.path.exists(self.nemo_metadata_path):
            try:
                self._keyfile.read(self.nemo_metadata_path, encoding="utf-8")
            except Exception:
                self._keyfile = self._new_keyfile_parser()

    # Sektionen im Keyfile, die KEINE Icons sind, sondern Layout-Konfiguration
    _KEYFILE_LAYOUT_PREFIX = "desktop-monitor-"

    def _get_special_items(self):
        """Liest die Positionen aller Spezial-Objekte (Volumes, home, trash,
        network) aus dem Keyfile. Diese Objekte sind KEINE Dateien auf dem
        Desktop und werden von Nemo ausschließlich hier gespeichert — gio kennt
        sie nicht.

        Gibt ein dict {sektionsname: {position, timestamp, monitor, icon-scale}}
        zurück.
        """
        self._load_keyfile()
        items = {}
        for section in self._keyfile.sections():
            if section.startswith(self._KEYFILE_LAYOUT_PREFIX):
                continue  # Layout-Konfig, kein Icon
            sect = self._keyfile[section]
            pos = sect.get("nemo-icon-position")
            if not pos:
                continue
            entry = {"metadata::nemo-icon-position": pos}
            ts = sect.get("nemo-icon-position-timestamp")
            if ts:
                entry["metadata::nemo-icon-position-timestamp"] = ts
            mon = sect.get("monitor")
            if mon is not None:
                entry["monitor"] = mon
            scale = sect.get("icon-scale")
            if scale is not None:
                entry["icon-scale"] = scale
            items[section] = entry
        return items

    def _restore_special_items(self, special_items, timestamp, log_callback=None):
        """Schreibt die Positionen der Spezial-Objekte (Volumes, home, trash,
        network) format-erhaltend zurück ins Keyfile.

        Nur bereits vorhandene Sektionen werden aktualisiert — ein Volume, das
        aktuell nicht auf dem Desktop liegt (nicht eingehängt), wird übersprungen.

        WICHTIG: Es wird pro Objekt ein frischer `nemo-icon-position-timestamp`
        gesetzt, ABER der globale `nemo-icon-view-layout-timestamp` wird NICHT
        angefasst. Ein frischer Item-Zeitstempel reicht, damit Nemo die Position
        als "neuer als das letzte Layout" akzeptiert und exakt übernimmt. Bumpt
        man dagegen den Layout-Zeitstempel selbst, erzwingt man bei
        keep-aligned=true einen kompletten Raster-Neuaufbau, der eng stehende
        Icons per Kollisionsauflösung wegschiebt — Icons landen "irgendwo".

        Gibt die Anzahl tatsächlich geschriebener Objekte zurück.
        """
        if not special_items:
            return 0
        if not os.path.exists(self.nemo_metadata_path):
            if log_callback:
                log_callback("  [Warnung] Nemo-Keyfile nicht gefunden — Spezial-Objekte übersprungen.")
            return 0

        # Backup anlegen (einmal je Wiederherstellung), damit ein Fehler das
        # Desktop-Layout nicht unwiederbringlich zerstört.
        try:
            shutil.copy2(self.nemo_metadata_path, self.nemo_metadata_path + ".bak")
        except Exception:
            pass

        # Frisch neu einlesen, um parallele Nemo-Schreibvorgänge nicht zu überschreiben
        self._load_keyfile()
        ts = str(timestamp)
        count = 0
        for section, meta in special_items.items():
            if section not in self._keyfile:
                if log_callback:
                    log_callback(f"  Überspringe Spezial-Objekt '{section}' (aktuell nicht auf dem Desktop)")
                continue
            pos = meta.get("metadata::nemo-icon-position")
            if not pos:
                continue
            self._keyfile[section]["nemo-icon-position"] = pos
            self._keyfile[section]["nemo-icon-position-timestamp"] = ts
            if log_callback:
                log_callback(f"Stelle Spezial-Objekt '{section}' auf {pos} wieder her...")
            count += 1

        # NICHT den Layout-Zeitstempel bumpen — siehe Docstring: das würde bei
        # keep-aligned=true einen destruktiven Raster-Neuaufbau auslösen.

        try:
            with open(self.nemo_metadata_path, "w", encoding="utf-8") as f:
                # space_around_delimiters=False -> GKeyFile-Format "key=value"
                self._keyfile.write(f, space_around_delimiters=False)
        except Exception as e:
            if log_callback:
                log_callback(f"  [Fehler] Keyfile-Schreiben fehlgeschlagen: {e}")
            return 0
        return count

    def _mounted_volume_names(self):
        """Namen aller Volumes/Mounts laut 'gio mount -l' oder None.

        Nemo legt im Keyfile für jedes jemals gezeigte Laufwerk eine Sektion
        '<name>.volume' an und räumt sie nie auf. Angezeigt wird aber nur, was
        gerade bekannt ist. None heißt "unbekannt" — dann wird nichts gefiltert,
        lieber ein Geist zu viel als ein echtes Laufwerk zu wenig.
        """
        try:
            out = subprocess.run(["gio", "mount", "-l"], capture_output=True,
                                 text=True, timeout=5)
        except Exception:
            return None
        if out.returncode != 0:
            return None
        names = set()
        for line in out.stdout.splitlines():
            m = re.match(r"\s*(?:Volume|Mount)\(\d+\):\s*(.*?)(?:\s+->\s+.*)?$", line)
            if m:
                names.add(m.group(1))
        return names or None

    def _is_ghost_volume(self, section, mounted):
        """True für eine Volume-Sektion, deren Laufwerk gerade nicht da ist."""
        return (mounted is not None and section.endswith(".volume")
                and section[:-len(".volume")] not in mounted)

    # ---------- Zustandsdatei ----------
    #
    # Merkt sich, welche Zeitstempel die App selbst geschrieben hat, und das
    # zuletzt erkannte Nemo-Raster — beides braucht detect_nemo_grid, um
    # eigene Schreibvorgänge nicht für Nemos Layout zu halten.

    _MAX_OWN_TIMESTAMPS = 50

    def _state_path(self):
        return os.path.join(self.config_dir, "state.json")

    def _load_state(self):
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, **changes):
        state = self._load_state()
        state.update(changes)
        try:
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4, ensure_ascii=False)
        except OSError:
            pass

    def _remember_own_timestamp(self, ts):
        own = self._load_state().get("app_timestamps", [])
        if ts not in own:
            own.append(ts)
        self._save_state(app_timestamps=own[-self._MAX_OWN_TIMESTAMPS:])

    # ---------- Nemos eigenes Zellraster ----------
    #
    # Nemo rastert bei keep-aligned=true JEDE geschriebene Position auf die
    # nächste eigene Zelle. Schreibt die App auf einem fremden Raster (z.B.
    # 100x100 ab 50,50), zieht Nemo die Symbole auf seine Zellen — aus gleichen
    # Abständen werden ungleiche. Deshalb ermitteln wir Nemos echtes Raster und
    # rastern darauf.

    _GRID_MIN = 20    # entspricht dem Minimum der Spinboxen in der GUI
    _GRID_MAX = 300

    def keep_aligned(self, reload=True):
        """True, wenn Nemo laut Keyfile selbst am Raster einrastet.

        reload=False nutzt das zuletzt geladene Keyfile — für Aufrufer, die es
        gerade selbst frisch eingelesen haben.
        """
        if reload:
            self._load_keyfile()
        for section in self._keyfile.sections():
            if not section.startswith(self._KEYFILE_LAYOUT_PREFIX):
                continue
            val = self._keyfile[section].get("nemo-icon-view-keep-aligned", "")
            if val.strip().lower() == "true":
                return True
        return False

    @classmethod
    def _spacing_gcd(cls, values):
        """Zellgröße einer Achse = größter gemeinsamer Teiler aller Abstände.

        Aus den Spalten 49/134/219 wird so 85. Gibt None zurück, wenn sich
        daraus keine plausible Zellgröße ableiten lässt.
        """
        if len(values) < 2:
            return None
        step = 0
        for a, b in zip(values, values[1:]):
            step = math.gcd(step, b - a)
        if step and cls._GRID_MIN <= step <= cls._GRID_MAX:
            return step
        return None

    @classmethod
    def _refine_cell(cls, cell, start, others):
        """Gleicht die Zellgröße einer Achse mit anderen Nemo-Layouts ab.

        Der GCD einer einzelnen Gruppe kann ein Vielfaches der echten Zelle
        sein: Liegen die Objekte nur in den Spalten 49 und 219, ergibt sich 170
        statt 85. Zeigt ein anderes Layout auf DEMSELBEN Gitter (Startpunkte
        passen zueinander) eine feinere Teilung, gilt die. Fehlt der Achse
        die Zellgröße ganz, wird sie aus einer anderen Gruppe übernommen.

        others = [(startwert, zellgröße)], gibt (zellgröße, startwert) zurück.
        """
        for o_start, o_cell in others:
            if not o_cell:
                continue
            if not cell:
                cell, start = o_cell, o_start
                continue
            g = math.gcd(cell, o_cell)
            if cls._GRID_MIN <= g < cell and (start - o_start) % g == 0:
                cell = g
        return cell, start

    def _grid_from_groups(self, entries, hint=None):
        """Raster EINES Monitors aus [(ts, x, y)] oder None.

        Zeitstempel-Gruppen vom jüngsten abwärts: Hat der Nutzer zuletzt nur
        ein einziges Symbol verschoben, trägt allein dieses den jüngsten
        Zeitstempel — daraus lässt sich nichts ablesen, also weiter zur
        nächstälteren Gruppe (Nemos letztes komplettes Layout). Die übrigen
        Gruppen und das zuletzt erkannte Raster (hint) dienen zum Abgleich.
        """
        groups = []
        for stamp in sorted({ts for ts, _, _ in entries}, reverse=True):
            xs = sorted({x for ts, x, _ in entries if ts == stamp})
            ys = sorted({y for ts, _, y in entries if ts == stamp})
            groups.append((stamp, xs, ys))

        best = None
        for group in groups:
            cx, cy = self._spacing_gcd(group[1]), self._spacing_gcd(group[2])
            if cx and cy:
                best = group
                break
            if (cx or cy) and best is None:
                best = group
        if best is None:
            return None

        stamp, xs, ys = best
        others_x = [(g[1][0], self._spacing_gcd(g[1])) for g in groups if g is not best]
        others_y = [(g[2][0], self._spacing_gcd(g[2])) for g in groups if g is not best]
        if hint:
            others_x.append((hint.get("origin_x"), hint.get("cell_x")))
            others_y.append((hint.get("origin_y"), hint.get("cell_y")))
        cell_x, start_x = self._refine_cell(self._spacing_gcd(xs), xs[0], others_x)
        cell_y, start_y = self._refine_cell(self._spacing_gcd(ys), ys[0], others_y)

        # Ursprung = erste Zelle, nicht das kleinste beobachtete Objekt: liegt
        # in Spalte 49 gerade nichts, wäre min(xs)=134 und alles links davon
        # würde von _snap auf 134 geklemmt.
        return {
            "cell_x": cell_x,
            "cell_y": cell_y,
            "origin_x": start_x % cell_x if cell_x else None,
            "origin_y": start_y % cell_y if cell_y else None,
            "stamp": stamp,
            "sample": sum(1 for ts, _, _ in entries if ts == stamp),
        }

    def detect_nemo_grid(self, items=None):
        """Leitet Nemos Zellraster aus dem Keyfile ab.

        Maßgeblich sind nur Objekte mit GLEICHEM Zeitstempel, bevorzugt dem
        jüngsten: Das sind die, die Nemo bei einem Layout gemeinsam gesetzt hat
        — sie liegen exakt auf seinem Raster. Ältere Einzeleinträge sind
        Altlasten und liegen daneben, sie würden die Erkennung verfälschen.

        Zeitstempel, die die App selbst geschrieben hat, zählen NICHT: Beim
        Laden ohne Raster bekommen alle Objekte denselben frischen Zeitstempel,
        auch auf Positionen neben Nemos Raster. Bleibt danach nichts übrig,
        gilt das zuletzt erkannte Raster aus der Zustandsdatei ("cached").

        Jeder Monitor wird getrennt ausgewertet (eigener Ursprung). Gibt das
        Raster des Monitors mit dem jüngsten Nemo-Layout zurück —
        {cell_x, cell_y, origin_x, origin_y, monitor, sample, origins} mit
        origins = {monitor: [x, y]}; cell_*/origin_* sind None, wenn eine
        Achse zu wenig Datenpunkte hat — oder None, wenn sich kein Raster
        ableiten lässt.
        """
        if items is None:
            items = self._get_special_items()
        state = self._load_state()
        own = set(state.get("app_timestamps", []))
        cached = state.get("nemo_grid")

        by_monitor = {}
        for meta in items.values():
            pos = self._parse_pos(meta.get("metadata::nemo-icon-position"))
            if pos is None:
                continue
            try:
                ts = int(meta.get("metadata::nemo-icon-position-timestamp", 0))
            except (TypeError, ValueError):
                ts = 0
            if ts in own:
                continue  # von der App geschrieben, nicht von Nemo platziert
            by_monitor.setdefault(str(meta.get("monitor", "0")), []).append((ts, pos[0], pos[1]))

        grids = {}
        for mon, entries in by_monitor.items():
            hint = cached if cached and cached.get("monitor") == mon else None
            grid = self._grid_from_groups(entries, hint)
            if grid:
                grid["monitor"] = mon
                grids[mon] = grid

        if not grids:
            if cached:
                cached["cached"] = True
            return cached

        grid = dict(max(grids.values(), key=lambda g: g["stamp"]))
        grid["origins"] = {mon: [g["origin_x"], g["origin_y"]] for mon, g in grids.items()}
        if grid != cached:
            self._save_state(nemo_grid=grid)
        return grid

    def _grid_origin(self, grid, monitor=None):
        """Ursprung fürs Rastern, pro Achse: der des Monitors, sonst Nemos
        erkannter Ursprung, sonst 50/50."""
        grid = grid or {}
        ox, oy = grid.get("origin_x"), grid.get("origin_y")
        if monitor is not None:
            mx, my = (grid.get("origins") or {}).get(str(monitor), (None, None))
            ox = ox if mx is None else mx
            oy = oy if my is None else my
        return (50 if ox is None else ox), (50 if oy is None else oy)

    @staticmethod
    def _parse_pos(pos_str):
        """'123,456' -> (123, 456); bei Unsinn None."""
        if not pos_str:
            return None
        try:
            x, y = pos_str.split(",")[:2]
            return int(x), int(y)
        except (ValueError, IndexError):
            return None

    def _get_icon_position(self, filename):
        """Liest die aktuelle Icon-Position einer Datei.
        Zuerst aus den GIO-Metadaten (das nutzt Nemo für Dateien),
        dann als Fallback aus dem GKeyFile. Gibt (x, y, monitor) oder None zurück."""
        # 1. GIO-Metadaten (maßgeblich für Dateien auf dem Desktop) — nur die
        # zwei Schlüssel, die hier gebraucht werden
        meta = self.get_gio_metadata(
            filename, keys=("metadata::nemo-icon-position", "metadata::monitor"))
        pos = self._parse_pos(meta.get("metadata::nemo-icon-position"))
        mon = meta.get("metadata::monitor", "0")
        # 2. Fallback: GKeyFile
        if pos is None and filename in self._keyfile:
            pos = self._parse_pos(self._keyfile[filename].get("nemo-icon-position"))
            mon = self._keyfile[filename].get("monitor", mon)
        if pos is None:
            return None
        return pos[0], pos[1], str(mon)

    def _set_icon_position(self, filename, x, y, timestamp=None):
        """Schreibt die Icon-Position via GIO-Metadaten — das ist der Store, den
        Nemo/Cinnamon für Dateien auf dem Desktop tatsächlich liest.

        Standardmäßig wird ein AKTUELLER Zeitstempel gesetzt, damit Nemo die
        Position als neueste akzeptiert und nicht durch ein Auto-Layout verwirft.
        """
        filepath = os.path.join(self.desktop_dir, filename)
        # lexists: auch (defekte) Symlinks berücksichtigen — deren Icon liegt
        # trotzdem auf dem Desktop.
        if not os.path.lexists(filepath):
            return False
        ts = str(timestamp) if timestamp else str(int(time.time()))
        try:
            subprocess.run(
                ["gio", "set", "-t", "string", filepath,
                 "metadata::nemo-icon-position", f"{x},{y}"],
                capture_output=True, timeout=5, check=True,
            )
            subprocess.run(
                ["gio", "set", "-t", "string", filepath,
                 "metadata::nemo-icon-position-timestamp", ts],
                capture_output=True, timeout=5,
            )
            return True
        except Exception:
            return False

    # ---------- GIO (maßgeblicher Store für Datei-Positionen) ----------

    # Nur die tatsächlich von Nemo/Cinnamon genutzten Keys lesen
    _GIO_KEYS = (
        "metadata::nemo-icon-position",
        "metadata::nemo-icon-position-timestamp",
        "metadata::monitor",
        "metadata::icon-scale",
        "metadata::nemo-icon-size",
        "metadata::icon-text-clip",
        "metadata::icon-alpha",
    )

    def get_gio_metadata(self, filename, keys=None):
        """Liest die GIO-Metadaten (inkl. Icon-Position) einer Desktop-Datei aus.
        Das ist der maßgebliche Store, den Nemo/Cinnamon für Dateien nutzt.

        Alle Schlüssel in EINEM 'gio info'-Aufruf — pro Schlüssel einen Prozess
        zu starten, macht das Ausrichten bei vielen Dateien spürbar zäh.
        """
        filepath = os.path.join(self.desktop_dir, filename)
        # lexists: auch (defekte) Symlinks berücksichtigen
        if not os.path.lexists(filepath):
            return {}

        keys = keys or self._GIO_KEYS
        try:
            output = subprocess.check_output(
                ["gio", "info", "-a", ",".join(keys), filepath],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode("utf-8")
        except Exception:
            return {}  # gio fehlgeschlagen — wie "keine Metadaten"

        metadata = {}
        for line in output.splitlines():
            line = line.strip()
            for key in keys:
                if line.startswith(key + ":"):
                    val = line[len(key) + 1 :].strip()
                    if val:  # Nur nicht-leere Werte speichern (fehlende lässt gio weg)
                        metadata[key] = val
                    break
        return metadata

    def get_desktop_files(self):
        """Gibt eine Liste aller relevanten Dateien auf dem Desktop zurück (ohne versteckte)."""
        if not os.path.exists(self.desktop_dir):
            return []

        files = []
        for name in os.listdir(self.desktop_dir):
            # Nur versteckte/temporäre Dateien ausschließen. Der eigene Starter
            # (Schreibtisch-Symbol-Manager.desktop) wird bewusst MITverwaltet —
            # sonst wird seine Position nie gespeichert/wiederhergestellt.
            if name.startswith("."):
                continue
            files.append(name)
        return files

    def save_profile(self, profile_name):
        """Speichert die aktuelle Icon-Anordnung in einem Profil.

        Liest die Positionen primär aus den GIO-Metadaten (das nutzt Nemo für
        Dateien); als Fallback wird das GKeyFile herangezogen.
        """
        files = self.get_desktop_files()
        profile_data = {
            "profile_name": profile_name,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "desktop_path": self.desktop_dir,
            "icons": {},
            # Spezial-Objekte (Volumes, home, trash, network) — leben nur im
            # Keyfile, nicht als Datei; gio kennt sie nicht.
            "special_items": self._get_special_items(),
        }

        for filename in files:
            # 1. GIO-Metadaten (maßgeblich für Dateien auf dem Desktop)
            meta = self.get_gio_metadata(filename)

            # 2. Fallback: GKeyFile, falls GIO keine Position kennt
            if "metadata::nemo-icon-position" not in meta and filename in self._keyfile:
                pos = self._keyfile[filename].get("nemo-icon-position")
                if pos:
                    meta = {"metadata::nemo-icon-position": pos}
                    ts = self._keyfile[filename].get("nemo-icon-position-timestamp")
                    if ts:
                        meta["metadata::nemo-icon-position-timestamp"] = ts

            if "metadata::nemo-icon-position" in meta:
                profile_data["icons"][filename] = meta

        filepath = os.path.join(self.profile_dir, f"{profile_name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=4, ensure_ascii=False)

        return len(profile_data["icons"]) + len(profile_data["special_items"])

    # ---------- Rastern ----------

    @staticmethod
    def _snap(value, step, offset):
        """Rundet auf den nächsten Rasterpunkt, nie kleiner als der Ursprung.

        Bewusst floor(x + 0.5) statt round(): round() rundet in Python bei
        exakt .5 zur GERADEN Zahl, gleich weit entfernte Symbole landen damit
        auf unterschiedlichen Zellen — genau die Unsymmetrie, die wir vermeiden.
        """
        cells = math.floor((value - offset) / step + 0.5)
        return offset + max(0, cells) * step

    def _snap_layout(self, entries, grid_x, grid_y, grid=None, log_callback=None):
        """Rastert [(kind, key, x, y, monitor)] und löst Kollisionen auf.

        Dateien und Spezial-Objekte teilen sich EINE Fläche und deshalb auch
        EIN Belegungsraster — sonst würde ein Laufwerk auf derselben Zelle
        landen wie eine Datei. Belegte Zellen weichen eine Zeile nach unten aus.
        Jeder Monitor ist eine eigene Fläche mit eigenem Ursprung: Koordinaten
        sind monitor-relativ, gleiche Werte auf zwei Monitoren kollidieren nicht.
        """
        occupied = set()
        result = []
        for kind, key, px, py, mon in sorted(entries, key=lambda e: (e[4], e[2], e[3])):
            offset_x, offset_y = self._grid_origin(grid, mon)
            rx = self._snap(px, grid_x, offset_x)
            ry = self._snap(py, grid_y, offset_y)
            while (mon, rx, ry) in occupied:
                ry += grid_y
            occupied.add((mon, rx, ry))
            if log_callback:
                log_callback(f"Richte '{key}' aus auf Rasterpunkt ({rx}, {ry})")
            result.append((kind, key, rx, ry, mon))
        return result

    def _resolve_collisions(self, entries, grid=None, log_callback=None):
        """Löst doppelt belegte Positionen auf, ohne sonst etwas zu verschieben.

        Nötig, weil Nemo im Keyfile Altlasten stehen lässt: zwei Volumes können
        auf derselben Position stehen (z.B. ein altes und ein aktuelles
        Laufwerk). Wird das so zurückgeschrieben, platziert Nemo nur eins davon
        und schiebt das andere selbst irgendwohin. Lieber deterministisch
        ausweichen und es im Log sagen. Kollisionen gibt es nur auf demselben
        Monitor.
        """
        step_y = (grid or {}).get("cell_y")
        seen = {}
        result = []
        for kind, key, x, y, mon in sorted(entries, key=lambda e: (e[4], e[2], e[3])):
            if (mon, x, y) in seen:
                other = seen[(mon, x, y)]
                if step_y:
                    ny = y
                    while (mon, x, ny) in seen:
                        ny += step_y
                    if log_callback:
                        log_callback(f"  [Kollision] '{key}' und '{other}' liegen im Profil "
                                     f"beide auf {x},{y} — '{key}' weicht auf {x},{ny} aus")
                    y = ny
                elif log_callback:
                    log_callback(f"  [Warnung] '{key}' und '{other}' liegen im Profil beide "
                                 f"auf {x},{y} — Nemo wird eines davon selbst verschieben")
            seen[(mon, x, y)] = key
            result.append((kind, key, x, y, mon))
        return result

    def _warn_on_grid_mismatch(self, grid_x, grid_y, grid, log_callback):
        """Warnt, wenn das gewählte Raster nicht zu Nemos Zellraster passt.

        Erwartet ein frisch geladenes Keyfile (Aufrufer haben gerade
        _get_special_items gelesen).
        """
        if not log_callback or not grid or not self.keep_aligned(reload=False):
            return
        cx, cy = grid.get("cell_x"), grid.get("cell_y")
        if (cx and grid_x % cx) or (cy and grid_y % cy):
            ox, oy = self._grid_origin(grid)
            log_callback(
                f"  [Warnung] Nemo rastert selbst nach (keep-aligned=true) auf "
                f"{cx or '?'}x{cy or '?'} px ab ({ox},{oy}). "
                f"Das gewählte Raster {grid_x}x{grid_y} ist kein Vielfaches davon — "
                f"Nemo wird die Symbole nachziehen und die Abstände werden ungleich."
            )

    def _special_entries(self, special_items, log_callback=None):
        """[(kind, key, x, y, monitor)] der Spezial-Objekte, die Nemo gerade zeigt.

        Ausgefiltert werden Sektionen, die im Live-Keyfile fehlen, und
        Volume-Altlasten, deren Laufwerk nicht eingehängt ist. Beide würden
        sonst beim Rastern bzw. in der Kollisionsauflösung Zellen belegen und
        echte Symbole verdrängen, obwohl sie nie sichtbar werden.
        Erwartet ein frisch geladenes Keyfile.
        """
        mounted = self._mounted_volume_names()
        entries = []
        for section, meta in special_items.items():
            if section not in self._keyfile or self._is_ghost_volume(section, mounted):
                if log_callback:
                    log_callback(f"  Überspringe Spezial-Objekt '{section}' "
                                 f"(aktuell nicht auf dem Desktop)")
                continue
            pos = self._parse_pos(meta.get("metadata::nemo-icon-position"))
            if pos:
                entries.append(("special", section, pos[0], pos[1],
                                str(meta.get("monitor", "0"))))
        return entries

    def align_icons_to_grid(self, grid_x, grid_y, log_callback=None):
        """Richtet den kompletten Schreibtisch am Raster aus.

        Erfasst Dateien (GIO) UND Spezial-Objekte wie Laufwerke, home, trash,
        network (Keyfile) — auf einem Schreibtisch voller eingehängter Laufwerke
        wäre eine reine Datei-Ausrichtung sonst mehr als die Hälfte blind.

        Gibt die Anzahl tatsächlich geschriebener Symbole zurück.
        """
        # Keyfile EINMAL lesen und für Erkennung, Warnung und Positionen nutzen
        items = self._get_special_items()
        grid = self.detect_nemo_grid(items)
        offset_x, offset_y = self._grid_origin(grid)
        if log_callback:
            log_callback(f"Berechne Raster-Ausrichtung ({grid_x}x{grid_y} Pixel) "
                         f"ab Ursprung ({offset_x},{offset_y})...")
        self._warn_on_grid_mismatch(grid_x, grid_y, grid, log_callback)

        entries = []
        for filename in self.get_desktop_files():
            # Aktuelle Position lesen (GIO zuerst, KeyFile als Fallback)
            pos = self._get_icon_position(filename)
            if pos is not None:
                entries.append(("file", filename) + pos)
        entries += self._special_entries(items, log_callback)

        if not entries:
            if log_callback:
                log_callback("Keine Symbole mit Position gefunden — nichts auszurichten.")
            return 0

        return self._write_positions(
            self._snap_layout(entries, grid_x, grid_y, grid, log_callback),
            log_callback=log_callback,
        )

    def _write_positions(self, entries, log_callback=None):
        """Schreibt [(kind, key, x, y, monitor)] und startet den Nemo-Schreibtisch neu.

        Geschrieben wird erst, wenn nemo-desktop steht: solange es läuft,
        rastert es frisch geschriebene Positionen sofort nach und kann eigene
        Werte zurückschreiben. Der Neustart läuft auch dann, wenn beim
        Schreiben etwas schiefgeht — sonst bliebe der Schreibtisch leer.
        """
        ts = int(time.time())
        # Vor dem Schreiben merken: detect_nemo_grid darf diesen Zeitstempel
        # später nicht für ein Nemo-Layout halten.
        self._remember_own_timestamp(ts)
        specials = {}
        count = 0
        failed = 0

        # Nur neu starten, was wir selbst angehalten haben — lief nemo-desktop
        # vorher nicht, legt ein Start einen fremden Schreibtisch über die Session.
        stopped = self._stop_nemo_desktop(log_callback)
        try:
            for kind, key, x, y, _mon in entries:
                if kind == "special":
                    specials[key] = {"metadata::nemo-icon-position": f"{x},{y}"}
                    continue
                if self._set_icon_position(key, x, y, timestamp=ts):
                    count += 1
                else:
                    failed += 1
                    if log_callback:
                        log_callback(f"  [Fehler] Position für '{key}' konnte nicht "
                                     f"geschrieben werden (gio set fehlgeschlagen)")
            count += self._restore_special_items(specials, ts, log_callback=log_callback)
        finally:
            if stopped:
                self._start_nemo_desktop(log_callback)

        if failed and log_callback:
            log_callback(f"  {failed} Symbol(e) konnten nicht geschrieben werden.")
        return count

    def restore_profile(self, profile_name, align_grid=None, log_callback=None):
        """Stellt die Icon-Anordnung aus einem Profil wieder her, optional am Raster ausgerichtet."""
        filepath = os.path.join(self.profile_dir, f"{profile_name}.json")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Profil '{profile_name}' existiert nicht.")

        with open(filepath, "r", encoding="utf-8") as f:
            profile_data = json.load(f)

        icons = profile_data.get("icons", {})
        special_items = profile_data.get("special_items", {})

        # Dateien und Spezial-Objekte in EINE Liste, damit Rasterung und
        # Kollisionsauflösung die gesamte Fläche sehen.
        entries = []
        for filename, meta in icons.items():
            file_path = os.path.join(self.desktop_dir, filename)
            # lexists: auch defekte Symlinks zählen — ihr Icon ist sichtbar
            if not os.path.lexists(file_path):
                if log_callback:
                    log_callback(f"Überspringe '{filename}' (Datei existiert nicht mehr)")
                continue
            pos = self._parse_pos(meta.get("metadata::nemo-icon-position"))
            if pos is None:
                if log_callback:
                    log_callback(f"  [Warnung] Keine gültige Position für '{filename}' im Profil")
                continue
            # Ältere Profile kennen den Monitor nicht — dann Monitor 0
            entries.append(("file", filename, pos[0], pos[1],
                            str(meta.get("metadata::monitor", "0"))))

        # Live-Keyfile EINMAL lesen: für die Raster-Erkennung und um
        # Spezial-Objekte zu verwerfen, die Nemo gerade nicht zeigt — BEVOR sie
        # in Rasterung oder Kollisionsauflösung Zellen belegen.
        live_items = self._get_special_items()
        entries += self._special_entries(special_items, log_callback)

        if not entries:
            if log_callback:
                log_callback("Profil enthält keine anwendbaren Positionen.")
            return 0

        grid = self.detect_nemo_grid(live_items)
        if align_grid:
            gx, gy = align_grid
            self._warn_on_grid_mismatch(gx, gy, grid, log_callback)
            entries = self._snap_layout(entries, gx, gy, grid, log_callback)
        else:
            entries = self._resolve_collisions(entries, grid, log_callback)
            if log_callback:
                for kind, key, x, y, _mon in entries:
                    log_callback(f"Stelle Position für '{key}' auf {x},{y} wieder her...")

        return self._write_positions(entries, log_callback=log_callback)

    def get_profiles(self):
        """Gibt eine Liste aller Profile mit Metadaten zurück."""
        profiles = []
        if not os.path.exists(self.profile_dir):
            return profiles
            
        for name in os.listdir(self.profile_dir):
            if name.endswith('.json'):
                path = os.path.join(self.profile_dir, name)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        profiles.append({
                            "name": data.get("profile_name", name[:-5]),
                            "timestamp": data.get("timestamp", "0000-00-00 00:00:00"),
                            "icon_count": len(data.get("icons", {})) + len(data.get("special_items", {})),
                            "filename": name
                        })
                except Exception:
                    pass
        # Nach Zeitstempel sortieren (neueste zuerst)
        profiles.sort(key=lambda x: x["timestamp"], reverse=True)
        return profiles

    def _nemo_desktop_running(self):
        """True, wenn ein nemo-desktop-Prozess läuft."""
        try:
            return subprocess.run(['pgrep', '-x', 'nemo-desktop'],
                                  capture_output=True).returncode == 0
        except Exception:
            return False

    def _wait_nemo_desktop_gone(self, seconds):
        """Wartet bis nemo-desktop weg ist; True, wenn es innerhalb der Zeit endete."""
        for _ in range(int(seconds * 10)):
            if not self._nemo_desktop_running():
                return True
            time.sleep(0.1)
        return not self._nemo_desktop_running()

    def _stop_nemo_desktop(self, log_callback=None):
        """Beendet den Nemo-Schreibtisch, BEVOR Positionen geschrieben werden.

        Solange nemo-desktop läuft, rastert es frisch geschriebene Positionen
        sofort nach und kann eigene Werte zurückschreiben — geschrieben wird
        deshalb erst, wenn es wirklich steht. Gibt True zurück, wenn es lief
        und ein Beenden angestoßen wurde — dann ist der Aufrufer für den
        Neustart zuständig.
        """
        if not self._nemo_desktop_running():
            if log_callback:
                log_callback("Hinweis: nemo-desktop läuft nicht, schreibe direkt.")
            return False

        if not self._which('nemo-desktop'):
            if log_callback:
                log_callback("Hinweis: nemo-desktop ist nicht installiert, kein Neustart möglich.")
            return False

        try:
            subprocess.run(['nemo-desktop', '-q'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if log_callback:
                log_callback(f"Hinweis: nemo-desktop konnte nicht beendet werden: {e}")
            return False

        # Warten bis der Prozess wirklich weg ist (max. 3 s) statt blind zu
        # schlafen — '-q' kehrt zurück, bevor der Prozess beendet ist. Hängt er,
        # mit SIGTERM nachhelfen: Ein noch laufender Prozess würde die frischen
        # Werte nachrastern und sich später unbemerkt verabschieden.
        if not self._wait_nemo_desktop_gone(3):
            subprocess.run(['pkill', '-TERM', '-x', 'nemo-desktop'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not self._wait_nemo_desktop_gone(3):
                if log_callback:
                    log_callback("  [Warnung] nemo-desktop lässt sich nicht beenden — "
                                 "Nemo kann die Positionen nachrastern.")
                return True

        if log_callback:
            log_callback("Nemo-Schreibtisch angehalten, schreibe Positionen...")
        return True

    def _start_nemo_desktop(self, log_callback=None):
        """Startet den Nemo-Schreibtisch wieder (liest die Metadaten neu ein)."""
        # Ein Prozess, der sich gerade noch beendet, darf den Neustart nicht
        # verhindern — sonst bleibt der Schreibtisch leer, sobald er weg ist.
        if not self._wait_nemo_desktop_gone(3):
            return
        if not self._which('nemo-desktop'):
            return
        try:
            subprocess.Popen(['nemo-desktop'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
            if log_callback:
                log_callback("Nemo-Schreibtisch erfolgreich neu gestartet!")
        except Exception as e:
            if log_callback:
                log_callback(f"Hinweis: Neustart des Nemo-Schreibtischs fehlgeschlagen: {e}")

    @staticmethod
    def _which(cmd):
        """Prüft ob ein Befehl im PATH existiert."""
        return shutil.which(cmd) is not None

    def delete_profile(self, profile_name):
        """Löscht ein Profil."""
        filepath = os.path.join(self.profile_dir, f"{profile_name}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
        return False


# Modernes Stylesheet (Catppuccin Mocha-Farben)
DARK_STYLE = """
QMainWindow {
    background-color: #1e1e2e;
}

QWidget {
    color: #cdd6f4;
    font-family: 'Segoe UI', 'Ubuntu', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}

/* Panel/Cards */
QFrame#leftPanel, QFrame#rightPanel {
    background-color: #181825;
    border-radius: 12px;
    border: 1px solid #313244;
}

/* Labels */
QLabel#titleLabel {
    font-size: 20px;
    font-weight: bold;
    color: #b4befe;
}

QLabel#subtitleLabel {
    font-size: 12px;
    color: #a6adc8;
}

QLabel#sectionTitle {
    font-size: 14px;
    font-weight: bold;
    color: #cba6f7;
}

QLabel#detailLabel {
    color: #bac2de;
    font-size: 13px;
}

/* List Widget */
QListWidget {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 5px;
    outline: 0;
}

QListWidget::item {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 10px;
    margin-bottom: 5px;
    color: #cdd6f4;
}

QListWidget::item:hover {
    background-color: #313244;
    border: 1px solid #b4befe;
}

QListWidget::item:selected {
    background-color: #b4befe;
    color: #11111b;
    border: 1px solid #b4befe;
}

/* Text Edit / Log */
QTextEdit {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 8px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    color: #a6e3a1; /* Terminal Green */
    padding: 8px;
}

/* Buttons */
QPushButton {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 10px 16px;
    font-weight: bold;
    color: #cdd6f4;
}

QPushButton:hover {
    background-color: #45475a;
    border: 1px solid #b4befe;
}

QPushButton:pressed {
    background-color: #585b70;
}

QPushButton#btnRestore {
    background-color: #b4befe;
    color: #11111b;
    border: 1px solid #b4befe;
}

QPushButton#btnRestore:hover {
    background-color: #cba6f7;
    border: 1px solid #cba6f7;
}

QPushButton#btnDelete {
    background-color: #f38ba8;
    color: #11111b;
    border: 1px solid #f38ba8;
}

QPushButton#btnDelete:hover {
    background-color: #eba0ac;
    border: 1px solid #eba0ac;
}

QPushButton#btnSave {
    background-color: #a6e3a1;
    color: #11111b;
    border: 1px solid #a6e3a1;
}

QPushButton#btnSave:hover {
    background-color: #94e2d5;
    border: 1px solid #94e2d5;
}

QPushButton#btnShortcut {
    background-color: #89b4fa;
    color: #11111b;
    border: 1px solid #89b4fa;
}

QPushButton#btnShortcut:hover {
    background-color: #74c7ec;
    border: 1px solid #74c7ec;
}
"""

class DesktopIconManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.manager = IconManager()
        self.initUI()
        self.load_profiles()

    def initUI(self):
        self.setWindowTitle(f"Schreibtisch Symbol-Manager v{__version__}")
        self.resize(850, 600) # Etwas vergrößert für das Raster-Panel
        self.setStyleSheet(DARK_STYLE)
        
        # Central Widget & Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # Splitter für die flexiblere Anordnung
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # LINKES PANEL: Profile
        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)
        left_layout.setSpacing(10)
        
        title_label = QLabel("Symbol-Manager")
        title_label.setObjectName("titleLabel")
        subtitle_label = QLabel("Schreibtisch-Layouts sichern & laden")
        subtitle_label.setObjectName("subtitleLabel")
        left_layout.addWidget(title_label)
        left_layout.addWidget(subtitle_label)
        
        sec_title = QLabel("Gespeicherte Profile")
        sec_title.setObjectName("sectionTitle")
        left_layout.addWidget(sec_title)
        
        self.profile_list = QListWidget()
        self.profile_list.itemSelectionChanged.connect(self.on_profile_selected)
        # Doppelklick auf ein Profil lädt es direkt
        self.profile_list.itemDoubleClicked.connect(self.restore_selected_layout)
        left_layout.addWidget(self.profile_list)
        
        self.btn_save_current = QPushButton("Aktuelles Layout speichern")
        self.btn_save_current.setObjectName("btnSave")
        self.btn_save_current.clicked.connect(self.save_current_layout)
        left_layout.addWidget(self.btn_save_current)
        
        splitter.addWidget(left_panel)

        # RECHTES PANEL: Aktionen, Details & Raster
        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(12)
        
        right_layout.addWidget(QLabel("Layout-Details", objectName="sectionTitle"))
        
        # Details
        self.lbl_profile_name = QLabel("Profil: Kein Profil ausgewählt", objectName="detailLabel")
        self.lbl_profile_date = QLabel("Gespeichert am: -", objectName="detailLabel")
        self.lbl_profile_count = QLabel("Anzahl Symbole: -", objectName="detailLabel")
        right_layout.addWidget(self.lbl_profile_name)
        right_layout.addWidget(self.lbl_profile_date)
        right_layout.addWidget(self.lbl_profile_count)
        
        # NEU: Raster-Optionen Panel
        right_layout.addWidget(QLabel("Raster-Optionen", objectName="sectionTitle"))
        
        grid_frame = QFrame()
        grid_frame.setStyleSheet("""
            QFrame {
                background-color: #11111b;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 10px;
            }
            QLabel {
                border: none;
                background-color: transparent;
                color: #bac2de;
            }
            QSpinBox {
                background-color: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 4px;
                color: #cdd6f4;
                min-width: 60px;
            }
            QCheckBox {
                spacing: 8px;
                background-color: transparent;
                color: #cdd6f4;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #313244;
                border-radius: 4px;
                background-color: #1e1e2e;
            }
            QCheckBox::indicator:checked {
                background-color: #b4befe;
                border: 1px solid #b4befe;
            }
        """)
        grid_layout = QVBoxLayout(grid_frame)
        grid_layout.setContentsMargins(10, 10, 10, 10)
        grid_layout.setSpacing(8)
        
        # Vorgabe ist Nemos eigenes Zellraster: Nur darauf bleiben die Symbole
        # liegen, wo sie hingeschrieben werden. Die Spinboxen bleiben als
        # Override nutzbar; ein abweichender Wert wird beim Ausrichten im Log
        # als Warnung gemeldet.
        self.nemo_grid = self.manager.detect_nemo_grid()
        default_x = (self.nemo_grid or {}).get("cell_x") or 100
        default_y = (self.nemo_grid or {}).get("cell_y") or 100

        spin_layout = QHBoxLayout()
        spin_layout.addWidget(QLabel("Spaltenbreite (X):"))
        self.spin_grid_x = QSpinBox()
        self.spin_grid_x.setRange(20, 300)
        self.spin_grid_x.setValue(default_x)
        self.spin_grid_x.setSuffix(" px")
        spin_layout.addWidget(self.spin_grid_x)

        spin_layout.addWidget(QLabel("Zeilenhöhe (Y):"))
        self.spin_grid_y = QSpinBox()
        self.spin_grid_y.setRange(20, 300)
        self.spin_grid_y.setValue(default_y)
        self.spin_grid_y.setSuffix(" px")
        spin_layout.addWidget(self.spin_grid_y)
        grid_layout.addLayout(spin_layout)

        if self.nemo_grid:
            ox, oy = self.manager._grid_origin(self.nemo_grid)
            # Nicht erkannte Achsen als '?' zeigen, nicht als Vorgabe 100
            hint = (f"Nemos eigenes Zellraster: {self.nemo_grid.get('cell_x') or '?'}x"
                    f"{self.nemo_grid.get('cell_y') or '?'} px ab "
                    f"({ox},{oy}).\n"
                    "Andere Werte zieht Nemo nach — die Abstände werden dann\n"
                    "ungleichmäßig.")
            self.spin_grid_x.setToolTip(hint)
            self.spin_grid_y.setToolTip(hint)
        
        self.chk_align_on_restore = QCheckBox("Layouts beim Laden am Raster ausrichten")
        self.chk_align_on_restore.setChecked(False)
        grid_layout.addWidget(self.chk_align_on_restore)
        
        self.btn_align_now = QPushButton("Sofort am Raster ausrichten")
        self.btn_align_now.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1;
                color: #11111b;
                border: 1px solid #a6e3a1;
                font-size: 12px;
                padding: 6px;
            }
            QPushButton:hover {
                background-color: #94e2d5;
                border: 1px solid #94e2d5;
            }
        """)
        self.btn_align_now.clicked.connect(self.align_current_desktop_to_grid)
        grid_layout.addWidget(self.btn_align_now)
        
        right_layout.addWidget(grid_frame)
        
        # Log-Fenster
        right_layout.addWidget(QLabel("Aktivitätsprotokoll", objectName="sectionTitle"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Aktionen werden hier protokolliert...")
        right_layout.addWidget(self.txt_log)
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_restore = QPushButton("Layout laden")
        self.btn_restore.setObjectName("btnRestore")
        self.btn_restore.setEnabled(False)
        self.btn_restore.clicked.connect(self.restore_selected_layout)
        
        self.btn_delete = QPushButton("Löschen")
        self.btn_delete.setObjectName("btnDelete")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_selected_profile)
        
        btn_layout.addWidget(self.btn_restore, 2)
        btn_layout.addWidget(self.btn_delete, 1)
        right_layout.addLayout(btn_layout)
        
        # Schreibtisch Starter Button
        self.btn_shortcut = QPushButton("Desktop-Starter erstellen")
        self.btn_shortcut.setObjectName("btnShortcut")
        self.btn_shortcut.clicked.connect(self.create_desktop_shortcut)
        right_layout.addWidget(self.btn_shortcut)
        
        splitter.addWidget(right_panel)
        
        # Standard-Aufteilung
        splitter.setSizes([350, 500])
        
        # DISPLAY-Check für Headless-Umgebungen
        display = os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')
        if not display:
            self.log("WARNUNG: Kein DISPLAY gesetzt.gio set/info funktioniert nur mit X11/Wayland-Session.")
            self.log("Starter-Button und Raster-Funktionen sind eingeschränkt nutzbar.")
        else:
            self.log(f"System initialisiert. Desktop-Pfad: {self.manager.desktop_dir}")
            if self.nemo_grid:
                ox, oy = self.manager._grid_origin(self.nemo_grid)
                source = ("zuletzt erkannt" if self.nemo_grid.get("cached")
                          else "erkannt")
                self.log(f"Nemo-Zellraster {source}: "
                         f"{self.nemo_grid.get('cell_x') or '?'}x{self.nemo_grid.get('cell_y') or '?'} px "
                         f"ab ({ox},{oy}) "
                         f"– als Raster-Vorgabe übernommen.")
            else:
                self.log("Hinweis: Nemos Zellraster nicht erkennbar, Vorgabe 100x100 ab (50,50).")

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(f"[{timestamp}] {message}")

    def load_profiles(self):
        self.profile_list.clear()
        profiles = self.manager.get_profiles()
        for p in profiles:
            item = QListWidgetItem(f"{p['name']}")
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(f"Gespeichert: {p['timestamp']}\nSymbole: {p['icon_count']}")
            self.profile_list.addItem(item)
            
        self.btn_restore.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.lbl_profile_name.setText("Profil: Kein Profil ausgewählt")
        self.lbl_profile_date.setText("Gespeichert am: -")
        self.lbl_profile_count.setText("Anzahl Symbole: -")

    def on_profile_selected(self):
        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            return
            
        profile_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        self.lbl_profile_name.setText(f"Profil: {profile_data['name']}")
        self.lbl_profile_date.setText(f"Gespeichert am: {profile_data['timestamp']}")
        self.lbl_profile_count.setText(f"Anzahl Symbole: {profile_data['icon_count']}")
        
        self.btn_restore.setEnabled(True)
        self.btn_delete.setEnabled(True)

    def save_current_layout(self):
        # DISPLAY prüfen
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            QMessageBox.warning(self, "Keine Session",
                                "Es wurde keine X11/Wayland-Session erkannt.\n"
                                "Das Speichern von Icon-Positionen benötigt eine aktive Desktop-Session.")
            return

        # Vorhandene Profile als Auswahl anbieten: entweder ein bestehendes
        # Profil aus der Liste wählen (überschreiben, ohne zu tippen) oder oben
        # einen neuen Namen eintippen.
        existing_names = [p["name"] for p in self.manager.get_profiles()]
        default_name = datetime.datetime.now().strftime("Layout %d.%m.%Y %H:%M")
        items = [default_name] + existing_names

        name, ok = QInputDialog.getItem(
            self,
            "Layout speichern",
            "Neuen Namen eingeben oder vorhandenes Profil\nzum Überschreiben auswählen:",
            items,
            0,        # Standard: neuer Name mit Zeitstempel
            True,     # editierbar -> neuer Name möglich
        )

        if not ok or not name.strip():
            return

        name = name.strip()
        safe_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c in ' ._-']).strip()
        if not safe_name:
            QMessageBox.critical(self, "Fehler", "Ungültiger Name.")
            return

        # Wenn ein bestehendes Profil getroffen wird: Überschreiben bestätigen
        if safe_name in existing_names:
            reply = QMessageBox.question(
                self,
                "Profil überschreiben",
                f"Das Profil '{safe_name}' existiert bereits.\n"
                f"Möchten Sie es mit dem aktuellen Layout überschreiben?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return


        self.log(f"Speichere aktuelles Layout unter '{safe_name}'...")
        try:
            count = self.manager.save_profile(safe_name)
            self.log(f"Erfolgreich gespeichert! {count} Symbole erfasst.")
            self.load_profiles()
            for i in range(self.profile_list.count()):
                item = self.profile_list.item(i)
                if item.text() == safe_name:
                    self.profile_list.setCurrentItem(item)
                    break
        except Exception as e:
            self.log(f"[Fehler] Speichern fehlgeschlagen: {e}")
            QMessageBox.critical(self, "Fehler", f"Fehler beim Speichern: {e}")

    def restore_selected_layout(self):
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            QMessageBox.warning(self, "Keine Session",
                                "Es wurde keine X11/Wayland-Session erkannt.\n"
                                "Das Wiederherstellen von Icon-Positionen benötigt eine aktive Desktop-Session.")
            return

        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            return
            
        profile_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        name = profile_data['name']
        
        align_grid = None
        if self.chk_align_on_restore.isChecked():
            align_grid = (self.spin_grid_x.value(), self.spin_grid_y.value())
            self.log(f"Stelle Layout '{name}' wieder her und richte am Raster aus...")
        else:
            self.log(f"Stelle Layout '{name}' wieder her...")
            
        self.txt_log.setEnabled(False)
        self.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        
        try:
            count = self.manager.restore_profile(name, align_grid=align_grid, log_callback=self.log)
            self.log(f"Layout '{name}' erfolgreich auf {count} Symbole angewendet!")
        except Exception as e:
            self.log(f"[Fehler] Wiederherstellung fehlgeschlagen: {e}")
            QMessageBox.critical(self, "Fehler", f"Wiederherstellung fehlgeschlagen: {e}")
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)
            self.txt_log.setEnabled(True)

    def align_current_desktop_to_grid(self):
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            QMessageBox.warning(self, "Keine Session",
                                "Es wurde keine X11/Wayland-Session erkannt.\n"
                                "Die Raster-Ausrichtung benötigt eine aktive Desktop-Session.")
            return

        gx = self.spin_grid_x.value()
        gy = self.spin_grid_y.value()
        
        self.log(f"Richte aktuellen Schreibtisch am {gx}x{gy} Raster aus...")
        self.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        
        try:
            count = self.manager.align_icons_to_grid(gx, gy, log_callback=self.log)
            self.log(f"Ausrichtung abgeschlossen! {count} Symbole am Raster ausgerichtet.")
        except Exception as e:
            self.log(f"[Fehler] Ausrichtung fehlgeschlagen: {e}")
            QMessageBox.critical(self, "Fehler", f"Ausrichtung fehlgeschlagen: {e}")
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)

    def delete_selected_profile(self):
        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            return
            
        profile_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        name = profile_data['name']
        
        reply = QMessageBox.question(
            self, 
            "Profil löschen", 
            f"Möchten Sie das Profil '{name}' wirklich unwiderruflich löschen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.manager.delete_profile(name):
                self.log(f"Profil '{name}' gelöscht.")
                self.load_profiles()
            else:
                self.log(f"[Fehler] Konnte Profil '{name}' nicht löschen.")

    def create_desktop_shortcut(self):
        # Verwende den Pfad aus der .desktop-Datei (fix), nicht __file__
        # Denn die App wird vom Nutzer von ~/.config/... aus gestartet
        script_path = os.path.expanduser("~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py")

        if not os.path.exists(script_path):
            QMessageBox.warning(self, "Starter-Fehler",
                                f"Skript nicht gefunden unter:\n{script_path}\n\n"
                                "Bitte kopieren Sie den Code dorthin oder passen Sie den Pfad an.")
            return

        desktop_dir = self.manager.desktop_dir
        shortcut_path = os.path.join(desktop_dir, "Schreibtisch-Symbol-Manager.desktop")

        # .desktop-Datei nicht ausführbar machen (0o644 reicht)
        desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Schreibtisch Symbol-Manager
Comment=Layouts der Symbole auf dem Schreibtisch sichern & laden
Exec=python3 {script_path}
Icon=preferences-desktop-icons
Terminal=false
Categories=Settings;Utility;
"""

        try:
            with open(shortcut_path, 'w', encoding='utf-8') as f:
                f.write(desktop_entry)

            os.chmod(shortcut_path, 0o644)
            self.log(f"Desktop-Starter unter '{shortcut_path}' erstellt.")
        except Exception as e:
            self.log(f"[Fehler] Starter-Erstellung fehlgeschlagen: {e}")
            QMessageBox.critical(self, "Fehler", f"Starter konnte nicht erstellt werden: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = DesktopIconManagerApp()
    ex.show()
    sys.exit(app.exec())
