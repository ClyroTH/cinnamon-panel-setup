# Cinnamon Panel Sync — Setup Guide
=====================================

## What this does

panel-sync.py is a lightweight daemon that keeps multiple Cinnamon taskbar panels
in sync. It was built for a dual-monitor setup where Cinnamon creates one
grouped-window-list panel per monitor.

Features:
  - Syncs pinned apps across ALL grouped-window-list instances in real time
  - Detects new monitors via hotplug and creates/configures panels automatically
  - Equalizes icon sizes (fullcolor + symbolic) across all panels at startup
  - Uses inotify (watchdog) instead of polling — zero CPU between changes
  - Uses /sys/class/drm instead of xrandr for monitor detection (0.24ms vs 290ms)
  - Uses Gio.Settings instead of gsettings subprocess calls — no process spawn overhead
  - 0.5s debounce prevents duplicate syncs when Cinnamon writes files atomically


## Why it is needed

Cinnamon only syncs pinned apps within a single panel instance. When you have
multiple monitors, each panel (grouped-window-list instance) manages its own
pinned apps independently. Pinning or unpinning an app on one panel does NOT
automatically update the others. This daemon watches for changes and propagates
them immediately.

Icon sizes are also stored per-panel and can drift — especially after adding a
new monitor. The daemon normalizes all panels to Panel 1's icon size at startup.


## Files

  panel-sync.py       — The daemon script
  panel-sync.desktop  — Autostart entry (placed in ~/.config/autostart/)
  README.md           — This file


## Installation

1. Copy the daemon to your local bin directory:

     cp panel-sync.py ~/.local/bin/panel-sync.py
     chmod +x ~/.local/bin/panel-sync.py

2. Install the Python dependency (watchdog):

     pip install watchdog --user
     # or on Debian/Ubuntu/Mint:
     sudo apt install python3-watchdog

3. Enable autostart by copying the desktop entry:

     cp panel-sync.desktop ~/.config/autostart/panel-sync.desktop

   **Important:** Open `~/.config/autostart/panel-sync.desktop` and replace
   `/home/YOUR_USERNAME/` with your actual home directory path.

4. Start it immediately without rebooting:

     python3 ~/.local/bin/panel-sync.py &


## How it works (technical)

  - At startup: reads all active grouped-window-list instance IDs from Cinnamon via
    D-Bus (org.Cinnamon.Eval), syncs icon sizes via Gio.Settings, then syncs
    pinned apps from the lowest instance ID to all others.

  - At runtime: inotify watches ~/.config/cinnamon/spices/grouped-window-list@cinnamon.org/
    for file changes. When a .json file changes (Cinnamon writes atomically via rename,
    so on_created and on_moved events are caught), a 0.5s debounce timer fires and
    propagates the change to all other instances via org.Cinnamon.Eval JavaScript.

  - Monitor hotplug: every 5 seconds, /sys/class/drm/*/status is read (near-instant).
    If the count changes, new panels are created in gsettings and icon sizes are
    re-synced.

  - Pinned app updates happen entirely within Cinnamon's main thread via:
      s.setValue('pinned-apps', [...])
      def.applet.pinnedFavorites.onFavoritesChange()
    No panel reload or Cinnamon restart is needed.


## Stopping / disabling

  - To stop the running daemon:
      pkill -f panel-sync.py

  - To disable autostart:
      rm ~/.config/autostart/panel-sync.desktop


## Logs

  - When running in foreground:
      python3 ~/.local/bin/panel-sync.py

  - To log to file:
      python3 ~/.local/bin/panel-sync.py > /tmp/panel-sync.log 2>&1 &
      tail -f /tmp/panel-sync.log
