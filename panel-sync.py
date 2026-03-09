#!/usr/bin/env python3
"""
Cinnamon Panel Sync Daemon
- Synct pinned-apps zwischen ALLEN grouped-window-list Instanzen (dynamisch)
- Erkennt neue Monitore per Hotplug und erstellt/konfiguriert Panels automatisch
- Gleicht Icon-Größen beim Start an
"""

import glob
import json
import os
import subprocess
import time
import logging
import threading
from gi.repository import Gio
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("panel-sync")

BASE          = os.path.expanduser("~/.config/cinnamon/spices/grouped-window-list@cinnamon.org")
DEBOUNCE      = 0.5  # Sekunden Wartezeit nach letzter Dateiänderung vor Sync
HOTPLUG_CHECK = 5    # Sekunden zwischen Monitor-Checks


# ── Cinnamon JS Eval ─────────────────────────────────────────────────────────

def eval_js(js):
    """JavaScript in Cinnamon auswerten. Gibt Ergebnis-String zurück."""
    try:
        r = subprocess.run(
            ["dbus-send", "--session", "--dest=org.Cinnamon", "--print-reply",
             "/org/Cinnamon", "org.Cinnamon.Eval", f"string:{js}"],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.splitlines():
            if "string" in line:
                m = line.strip()
                m = m[m.find('"'):]
                return m.strip('"')
    except Exception:
        pass
    return ""


# ── gsettings via Gio (kein Subprocess) ──────────────────────────────────────

_cinnamon_settings = None

def _settings():
    global _cinnamon_settings
    if _cinnamon_settings is None:
        _cinnamon_settings = Gio.Settings.new("org.cinnamon")
    return _cinnamon_settings


def gsettings_get_list(key):
    return list(_settings().get_value(key))


def gsettings_set_value(key, value):
    from gi.repository import GLib
    _settings().set_value(key, GLib.Variant("as", value))


# ── Instanzen & Dateien ──────────────────────────────────────────────────────

def get_instances_from_cinnamon():
    """Alle laufenden grouped-window-list Instanz-IDs aus Cinnamon."""
    result = eval_js(
        "Main.AppletManager.definitions"
        ".filter(d => d.uuid === 'grouped-window-list@cinnamon.org')"
        ".map(d => String(d.applet_id)).join(',')"
    )
    return [x.strip() for x in result.split(",") if x.strip()] if result else []


def instance_file(instance_id):
    return os.path.join(BASE, f"{instance_id}.json")


def read_pinned(instance_id):
    path = instance_file(instance_id)
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("pinned-apps", {}).get("value", [])
    except Exception as e:
        log.warning(f"Lesen fehlgeschlagen ({path}): {e}")
        return None


# ── Pinned-Apps setzen ───────────────────────────────────────────────────────

def set_pinned(instance_id, pinned):
    """setValue + onFavoritesChange() direkt im laufenden Applet."""
    apps_json = json.dumps(pinned)
    js = f"""try {{
    let mgr = Main.settingsManager.uuids['grouped-window-list@cinnamon.org'];
    let s = mgr['{instance_id}'];
    if (!s) throw new Error('no settings for {instance_id}');
    s.setValue('pinned-apps', {apps_json});
    let def = Main.AppletManager.definitions.find(
        d => d.uuid === 'grouped-window-list@cinnamon.org' && String(d.applet_id) === '{instance_id}');
    if (!def) throw new Error('no applet for {instance_id}');
    def.applet.pinnedFavorites.onFavoritesChange();
    'OK';
}} catch(e) {{ 'ERR:' + e.message; }}"""
    result = eval_js(js)
    if result == "OK":
        return True
    log.warning(f"set_pinned({instance_id}): {result}")
    return False


def sync_to_all(source_id, pinned, targets):
    for tid in targets:
        if set_pinned(tid, pinned):
            time.sleep(0.1)
            log.info(f"  → Instanz {tid} aktualisiert")


# ── Icon-Größen ──────────────────────────────────────────────────────────────

def _sync_zone_sizes(key):
    """Gleicht panel-zone-*-icon-sizes auf Panel 1 als Referenz an."""
    try:
        sizes = json.loads(gsettings_get_list(key)[0] if False else
                           _settings().get_value(key).unpack())
    except Exception:
        return 0

    if not sizes:
        return 0

    ref = next((s for s in sizes if s.get("panelId") == 1), sizes[0])
    panel_ids = [int(p.split(":")[0]) for p in gsettings_get_list("panels-enabled")]

    new_sizes = {s["panelId"]: s for s in sizes}
    changed = False
    for pid in panel_ids:
        entry = new_sizes.get(pid)
        if entry is None or any(entry.get(z) != ref.get(z) for z in ("left", "center", "right")):
            new_sizes[pid] = {"panelId": pid, "left": ref["left"],
                              "center": ref["center"], "right": ref["right"]}
            changed = True

    if changed:
        gsettings_set_value(key, [json.dumps(list(new_sizes.values()))])
        log.info(f"{key}: right={ref['right']}px für alle Panels angeglichen")

    return ref.get("right") or 0


def sync_icon_sizes():
    """Icon-Größen aller Panels (fullcolor + symbolic) auf Panel 1 angleichen."""
    right_size = _sync_zone_sizes("panel-zone-icon-sizes")
    _sync_zone_sizes("panel-zone-symbolic-icon-sizes")
    if right_size:
        sync_applet_icon_sizes(right_size)


def sync_applet_icon_sizes(size):
    """Setzt icon_size direkt auf Applet-Icons aller Panels."""
    js = f"""try {{
    ['network@cinnamon.org','sound@cinnamon.org'].forEach(uuid => {{
        Main.AppletManager.definitions.filter(d => d.uuid === uuid).forEach(d => {{
            d.applet._iconSize = {size};
            let icon = d.applet._applet_icon;
            if (icon) icon.set_icon_size({size});
        }});
    }});
    'OK';
}} catch(e) {{ 'ERR:' + e.message; }}"""
    result = eval_js(js)
    if result == "OK":
        log.info(f"Applet-Icon-Größen auf {size}px gesetzt")
    else:
        log.warning(f"sync_applet_icon_sizes: {result}")


# ── Monitor-Hotplug & Panel-Erstellung ───────────────────────────────────────

def get_monitor_count():
    try:
        return sum(
            1 for f in glob.glob("/sys/class/drm/*/status")
            if open(f).read().strip() == "connected"
        ) or 1
    except Exception:
        return 1


def get_panels_config():
    panels = []
    for p in gsettings_get_list("panels-enabled"):
        parts = p.strip().split(":")
        if len(parts) >= 3:
            panels.append((int(parts[0]), int(parts[1]), parts[2]))
    return panels


def create_panels_for_new_monitors(source_instance):
    monitor_count = get_monitor_count()
    panels = get_panels_config()
    existing_monitors = {mon for _, mon, _ in panels}
    next_id = max((pid for pid, _, _ in panels), default=0) + 1

    ref_panel = next(((pid, mon, pos) for pid, mon, pos in panels if mon == 0), None)
    if not ref_panel:
        return False

    heights = {}
    for h in gsettings_get_list("panels-height"):
        if ":" in h:
            pid_str, hval = h.split(":", 1)
            try:
                heights[int(pid_str.strip())] = hval.strip()
            except ValueError:
                pass
    ref_height = heights.get(ref_panel[0], "40")

    new_panels_added = False
    for mon in range(monitor_count):
        if mon in existing_monitors:
            continue
        log.info(f"Neuer Monitor {mon} erkannt → erstelle Panel {next_id}")
        panels.append((next_id, mon, ref_panel[2]))
        heights[next_id] = ref_height
        next_id += 1
        new_panels_added = True

    if not new_panels_added:
        return False

    gsettings_set_value("panels-enabled",
                        [f"{pid}:{mon}:{pos}" for pid, mon, pos in panels])
    gsettings_set_value("panels-height",
                        [f"{pid}:{h}" for pid, h in sorted(heights.items())])
    log.info("Neue Panels erstellt, warte auf Cinnamon...")
    time.sleep(3)
    return True


# ── Startup ──────────────────────────────────────────────────────────────────

def startup_sync():
    instances = get_instances_from_cinnamon()
    if not instances:
        log.warning("Keine grouped-window-list Instanzen gefunden")
        return

    source_id = min(instances, key=int)
    log.info(f"Instanzen gefunden: {instances} (Quelle: {source_id})")

    sync_icon_sizes()
    time.sleep(2)
    sync_icon_sizes()
    log.info("Startup: Icon-Größen angeglichen")

    create_panels_for_new_monitors(source_id)

    instances = get_instances_from_cinnamon()
    source_id = min(instances, key=int)
    targets = [i for i in instances if i != source_id]

    source_pinned = read_pinned(source_id)
    if source_pinned is None:
        return

    log.info(f"Startup: sync {len(source_pinned)} Apps von Instanz {source_id} → {targets}")
    set_pinned(source_id, source_pinned)
    sync_to_all(source_id, source_pinned, targets)

    return instances


# ── inotify Handler ──────────────────────────────────────────────────────────

class InstanceFileHandler(FileSystemEventHandler):
    """Reagiert auf Dateiänderungen per inotify — mit Debounce."""

    def __init__(self, instances_ref):
        self._instances_ref = instances_ref  # mutable list [instances]
        self._timers = {}
        self._lock = threading.Lock()

    def _handle(self, path):
        if not path.endswith(".json"):
            return
        instance_id = os.path.basename(path).replace(".json", "")
        instances = self._instances_ref[0]
        if instance_id not in instances:
            return
        with self._lock:
            if instance_id in self._timers:
                self._timers[instance_id].cancel()
            t = threading.Timer(DEBOUNCE, self._process, args=(instance_id,))
            self._timers[instance_id] = t
            t.start()

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.dest_path)

    def _process(self, changed_id):
        instances = self._instances_ref[0]
        pinned = read_pinned(changed_id)
        if pinned is None:
            return
        targets = [i for i in instances if i != changed_id]
        if not any(read_pinned(t) != pinned for t in targets):
            return
        log.info(f"Änderung auf Instanz {changed_id} ({len(pinned)} Apps) → sync zu {targets}")
        sync_to_all(changed_id, pinned, targets)


# ── Hauptschleife ────────────────────────────────────────────────────────────

def main():
    log.info("Panel-Sync gestartet (dynamisch, multi-monitor)")
    time.sleep(2)

    instances = startup_sync() or get_instances_from_cinnamon()
    instances_ref = [instances]  # mutable Referenz für Handler

    log.info(f"Überwache {len(instances)} Instanzen via inotify: {instances}")

    handler = InstanceFileHandler(instances_ref)
    observer = Observer()
    observer.schedule(handler, BASE, recursive=False)
    observer.start()

    last_monitor_count = get_monitor_count()

    try:
        while True:
            time.sleep(HOTPLUG_CHECK)
            monitor_count = get_monitor_count()
            if monitor_count == last_monitor_count:
                continue

            log.info(f"Monitor-Änderung: {last_monitor_count} → {monitor_count}")
            last_monitor_count = monitor_count

            if monitor_count > len(instances_ref[0]):
                create_panels_for_new_monitors(min(instances_ref[0], key=int))
                time.sleep(1)

            instances_ref[0] = get_instances_from_cinnamon()
            sync_icon_sizes()

            source_id = min(instances_ref[0], key=int)
            source_pinned = read_pinned(source_id)
            if source_pinned:
                targets = [i for i in instances_ref[0] if i != source_id]
                sync_to_all(source_id, source_pinned, targets)

    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
