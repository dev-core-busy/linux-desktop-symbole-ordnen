# linux-desktop-symbole-ordnen

**Schreibtisch Symbol-Manager** – eine kleine GUI zum **Sichern und
Wiederherstellen der Icon-Positionen** auf dem Linux-Desktop
(Cinnamon / Nemo). Layouts lassen sich als benannte Profile speichern und
jederzeit exakt wiederherstellen – optional am Raster ausgerichtet.

> Die Oberfläche ist auf Deutsch.

![Version](https://img.shields.io/badge/version-1.0-blue)

## Funktionen

- 📌 Aktuelle Anordnung der Desktop-Symbole als **Profil** speichern
- ♻️ Gespeicherte Profile per Klick (oder **Doppelklick**) wiederherstellen
- 🧲 Symbole **am Raster ausrichten** (Spaltenbreite/Zeilenhöhe einstellbar),
  sofort oder beim Laden eines Profils
- 💾 Beim Speichern ein **vorhandenes Profil zum Überschreiben auswählen**
  (mit Rückfrage) oder neuen Namen vergeben
- 🚀 Desktop-Starter (`.desktop`) direkt aus der App erstellen

## Wie es funktioniert

Nemo/Cinnamon speichert die Icon-Position regulärer Desktop-Dateien in den
**GIO-Metadaten** der jeweiligen Datei (`gio set metadata::nemo-icon-position`).
Genau dort schreibt und liest der Manager – deshalb werden Positionen beim
Wiederherstellen zuverlässig übernommen. Nach dem Anwenden wird `nemo-desktop`
kurz neu gestartet, damit die Änderungen sofort sichtbar sind. Profile werden
als JSON unter `~/.config/schreibtisch-symbol-manager/profiles/` abgelegt.

## Voraussetzungen

- Linux mit **Cinnamon / Nemo** (X11 oder Wayland-Session)
- Python 3
- PyQt6
- System-Tools: `gio`, `xdg-user-dir`, `nemo-desktop` (bei Cinnamon vorhanden)

```bash
pip install -r requirements.txt
```

## Installation & Start

```bash
git clone https://github.com/dev-core-busy/linux-desktop-symbole-ordnen.git
cd linux-desktop-symbole-ordnen
python3 desktop_icon_manager.py
```

Für einen dauerhaften Starter empfiehlt sich, die App nach
`~/.config/schreibtisch-symbol-manager/` zu kopieren und in der App auf
**„Desktop-Starter erstellen"** zu klicken – dieser legt eine passende
`.desktop`-Datei mit korrekten Pfaden an.

```bash
mkdir -p ~/.config/schreibtisch-symbol-manager
cp desktop_icon_manager.py app_icon.png ~/.config/schreibtisch-symbol-manager/
python3 ~/.config/schreibtisch-symbol-manager/desktop_icon_manager.py
```

## Hinweise

- Getestet mit Cinnamon/Nemo. Andere Desktop-Umgebungen (GNOME, KDE) werden
  nicht unterstützt.
- Die Aktionen benötigen eine aktive Grafik-Session (`DISPLAY`/`WAYLAND_DISPLAY`).

## Lizenz

MIT – siehe [LICENSE](LICENSE).
