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

        # GKeyFile für Nemo-Desktop-Metadaten (INIs-Datei, die Cinnamon NACH DEMNACH LIEST!)
        self.nemo_metadata_path = os.path.expanduser("~/.config/nemo/desktop-metadata")
        self._keyfile = configparser.ConfigParser()
        self._load_keyfile()

    # ---------- GKeyFile (nur Lese-Fallback) ----------
    #
    # WICHTIG: Für reguläre Dateien auf dem Desktop liest Nemo/Cinnamon die
    # Icon-Positionen NICHT aus diesem GKeyFile, sondern aus den GIO-Metadaten
    # (gvfs) der jeweiligen Datei. Das GKeyFile enthält nativ nur Spezialobjekte
    # (home, trash, Volumes, network). Wir schreiben Positionen deshalb via GIO
    # (siehe _set_icon_position) und nutzen das KeyFile nur noch als Lese-Fallback
    # für Alt-Profile.

    def _load_keyfile(self):
        """Lädt die Nemo-Desktop-Metadaten aus dem GKeyFile (nur zum Lesen)."""
        if os.path.exists(self.nemo_metadata_path):
            try:
                self._keyfile.read(self.nemo_metadata_path, encoding="utf-8")
            except Exception:
                self._keyfile = configparser.ConfigParser()
        else:
            self._keyfile = configparser.ConfigParser()

    def _get_icon_position(self, filename):
        """Liest die aktuelle Icon-Position einer Datei.
        Zuerst aus den GIO-Metadaten (das nutzt Nemo für Dateien),
        dann als Fallback aus dem GKeyFile. Gibt (x, y) oder (None, None) zurück."""
        # 1. GIO-Metadaten (maßgeblich für Dateien auf dem Desktop)
        meta = self.get_gio_metadata(filename)
        pos_str = meta.get("metadata::nemo-icon-position", "")
        # 2. Fallback: GKeyFile
        if not pos_str and filename in self._keyfile:
            pos_str = self._keyfile[filename].get("nemo-icon-position", "")
        if pos_str:
            try:
                parts = pos_str.split(",")
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return None, None

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

    def get_gio_metadata(self, filename):
        """Liest die GIO-Metadaten (inkl. Icon-Position) einer Desktop-Datei aus.
        Das ist der maßgebliche Store, den Nemo/Cinnamon für Dateien nutzt."""
        filepath = os.path.join(self.desktop_dir, filename)
        # lexists: auch (defekte) Symlinks berücksichtigen
        if not os.path.lexists(filepath):
            return {}

        # Nur die tatsächlich von Nemo/Cinnamon genutzten Keys lesen
        relevant_keys = [
            "metadata::nemo-icon-position",
            "metadata::nemo-icon-position-timestamp",
            "metadata::nemo-icon-size",
            "metadata::icon-text-clip",
            "metadata::icon-alpha",
        ]

        metadata = {}
        for key in relevant_keys:
            try:
                output = subprocess.check_output(
                    ["gio", "info", "-a", key, filepath],
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8")

                for line in output.splitlines():
                    line = line.strip()
                    if line.startswith(key + ":"):
                        val = line[len(key) + 1 :].strip()
                        if val:  # Nur nicht-leere Werte speichern
                            metadata[key] = val
            except Exception:
                pass  # Key existiert nicht oder gio fehgeschlagen — einfach ignorieren

        return metadata

    def get_desktop_files(self):
        """Gibt eine Liste aller relevanten Dateien auf dem Desktop zurück (ohne versteckte)."""
        if not os.path.exists(self.desktop_dir):
            return []

        files = []
        for name in os.listdir(self.desktop_dir):
            # Schließe versteckte Dateien, temporäre Dateien und den Starter selbst aus
            if name.startswith("."):
                continue
            if name == "Schreibtisch-Symbol-Manager.desktop":
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

        return len(profile_data["icons"])

    def align_icons_to_grid(self, grid_x, grid_y, custom_icons=None, log_callback=None):
        """Richtet Icons an einem Raster aus. Wenn custom_icons gegeben ist, wird dieses dict ausgerichtet."""
        if log_callback:
            log_callback(f"Berechne Raster-Ausrichtung ({grid_x}x{grid_y} Pixel) mit 50px Offset...")

        offset_x = 50
        offset_y = 50

        # ---- Modus A: custom_icons dict aus Profil wird ausgerichtet ----
        if custom_icons is not None:
            aligned_icons = {}
            occupied = set()

            def get_coords(item):
                meta = item[1]
                pos_str = meta.get("metadata::nemo-icon-position", "0,0")
                try:
                    parts = pos_str.split(",")
                    return (int(parts[0]), int(parts[1]))
                except Exception:
                    return (0, 0)

            sorted_icons = sorted(custom_icons.items(), key=get_coords)

            for filename, meta in sorted_icons:
                pos_str = meta.get("metadata::nemo-icon-position", "0,0")
                try:
                    px, py = map(int, pos_str.split(","))
                    rx = int(round((px - offset_x) / grid_x) * grid_x + offset_x)
                    ry = int(round((py - offset_y) / grid_y) * grid_y + offset_y)
                    rx = max(offset_x, rx)
                    ry = max(offset_y, ry)
                    while (rx, ry) in occupied:
                        ry += grid_y
                    occupied.add((rx, ry))
                    new_meta = meta.copy()
                    new_meta["metadata::nemo-icon-position"] = f"{rx},{ry}"
                    aligned_icons[filename] = new_meta
                except Exception as e:
                    if log_callback:
                        log_callback(f"  Fehler bei {filename}: {e}")
                    aligned_icons[filename] = meta

            return aligned_icons

        # ---- Modus B: Direkt auf dem aktuellen Schreibtisch ----
        files = self.get_desktop_files()
        icons_data = []

        for filename in files:
            # Aktuelle Position lesen (GIO zuerst, KeyFile als Fallback)
            x, y = self._get_icon_position(filename)
            if x is not None:
                icons_data.append((filename, x, y))

        icons_data.sort(key=lambda x: (x[1], x[2]))

        occupied = set()
        count = 0

        for filename, px, py in icons_data:
            rx = int(round((px - offset_x) / grid_x) * grid_x + offset_x)
            ry = int(round((py - offset_y) / grid_y) * grid_y + offset_y)
            rx = max(offset_x, rx)
            ry = max(offset_y, ry)
            while (rx, ry) in occupied:
                ry += grid_y
            occupied.add((rx, ry))

            if log_callback:
                log_callback(f"Richte '{filename}' aus auf Rasterpunkt ({rx}, {ry})")

            # Via GIO schreiben (das liest Nemo für Dateien), frischer Zeitstempel
            self._set_icon_position(filename, rx, ry)
            count += 1

        if count > 0:
            if log_callback:
                log_callback("Starte Nemo-Schreibtisch neu, um Rasterung anzuwenden...")
            self._restart_nemo_desktop(log_callback)

        return count

    def restore_profile(self, profile_name, align_grid=None, log_callback=None):
        """Stellt die Icon-Anordnung aus einem Profil wieder her, optional am Raster ausgerichtet."""
        filepath = os.path.join(self.profile_dir, f"{profile_name}.json")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Profil '{profile_name}' existiert nicht.")

        with open(filepath, "r", encoding="utf-8") as f:
            profile_data = json.load(f)

        icons = profile_data.get("icons", {})

        # Falls Rasterausrichtung beim Laden gewünscht ist
        if align_grid:
            gx, gy = align_grid
            icons = self.align_icons_to_grid(gx, gy, custom_icons=icons, log_callback=log_callback)

        count = 0
        for filename, meta in icons.items():
            file_path = os.path.join(self.desktop_dir, filename)
            # Nur anwenden, wenn das Symbol noch auf dem Desktop liegt
            # (lexists: auch defekte Symlinks zählen — ihr Icon ist sichtbar)
            if os.path.lexists(file_path):
                if log_callback:
                    log_callback(f"Stelle Position für '{filename}' wieder her...")

                # Position aus dem Profil extrahieren
                pos_str = meta.get("metadata::nemo-icon-position")

                if pos_str:
                    try:
                        x, y = map(int, pos_str.split(","))
                        # Frischer Zeitstempel (Default), damit Nemo die Position
                        # als aktuell akzeptiert und nicht durch Auto-Layout verwirft
                        self._set_icon_position(filename, x, y)
                        count += 1
                    except (ValueError, IndexError):
                        if log_callback:
                            log_callback(f"  [Fehler] Ungültige Position '{pos_str}' für '{filename}'")
                else:
                    if log_callback:
                        log_callback(f"  [Warnung] Keine Position für '{filename}' im Profil")
            else:
                if log_callback:
                    log_callback(f"Überspringe '{filename}' (Datei existiert nicht mehr)")

        # Nemo-Desktop neu starten, damit die Änderungen wirksam werden
        if log_callback:
            log_callback("Starte Nemo-Schreibtisch neu, um Änderungen anzuwenden...")
        self._restart_nemo_desktop(log_callback)

        return count

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
                            "icon_count": len(data.get("icons", {})),
                            "filename": name
                        })
                except Exception:
                    pass
        # Nach Zeitstempel sortieren (neueste zuerst)
        profiles.sort(key=lambda x: x["timestamp"], reverse=True)
        return profiles

    def _restart_nemo_desktop(self, log_callback=None):
        """Startet nemo-desktop neu, um Icon-Position-Änderungen wirksam zu machen.

        Graceful-Fallback: Wenn nemo-desktop nicht läuft, wird nicht neu gestartet.
        """
        try:
            # Prüfen ob nemo-desktop läuft
            result = subprocess.run(['pgrep', '-x', 'nemo-desktop'],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                # Nicht vorhanden → nichts zu tun
                if log_callback:
                    log_callback("Hinweis: nemo-desktop läuft nicht, nichts neu zu starten.")
                return

            # Existiert der Befehl?
            if not self._which('nemo-desktop'):
                if log_callback:
                    log_callback("Hinweis: nemo-desktop ist nicht installiert, kein Neustart möglich.")
                return

            # Beenden
            subprocess.run(['nemo-desktop', '-q'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if log_callback:
                log_callback(f"Hinweis: nemo-desktop konnte nicht beendet werden: {e}")

        # Kurze Wartezeit
        import time
        time.sleep(0.5)

        # Neu starten (nur wenn es existiert)
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
                log_callback(f"Hinweis: Nemo-Schreibtstart fehlgeschlagen: {e}")

    def _which(self, cmd):
        """Prüft ob ein Befehl im PATH existiert."""
        return subprocess.run(['which', cmd], capture_output=True).returncode == 0

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
        
        spin_layout = QHBoxLayout()
        spin_layout.addWidget(QLabel("Spaltenbreite (X):"))
        self.spin_grid_x = QSpinBox()
        self.spin_grid_x.setRange(20, 300)
        self.spin_grid_x.setValue(100)
        self.spin_grid_x.setSuffix(" px")
        spin_layout.addWidget(self.spin_grid_x)
        
        spin_layout.addWidget(QLabel("Zeilenhöhe (Y):"))
        self.spin_grid_y = QSpinBox()
        self.spin_grid_y.setRange(20, 300)
        self.spin_grid_y.setValue(100)
        self.spin_grid_y.setSuffix(" px")
        spin_layout.addWidget(self.spin_grid_y)
        grid_layout.addLayout(spin_layout)
        
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
