"""
Test configuration for cinnamon-panel-setup.

panel-sync.py has a hyphen in its filename (not importable via normal `import`),
and depends on gi (GNOME introspection) and watchdog which require a live desktop.
We mock all external dependencies before loading the module.
"""
import importlib.util
import os
import sys
import unittest.mock as mock

import pytest

# ── Mock external dependencies before loading the module ─────────────────────

# gi / GLib / Gio — not available without a running GNOME session
_mock_gi = mock.MagicMock()
_mock_gi_repository = mock.MagicMock()
sys.modules["gi"] = _mock_gi
sys.modules["gi.repository"] = _mock_gi_repository
sys.modules["gi.repository.Gio"] = _mock_gi_repository.Gio
sys.modules["gi.repository.GLib"] = _mock_gi_repository.GLib

# watchdog.observers — needs inotify / kernel support
sys.modules["watchdog"] = mock.MagicMock()
sys.modules["watchdog.observers"] = mock.MagicMock()

# watchdog.events — InstanceFileHandler inherits from FileSystemEventHandler,
# so we provide a real (trivial) base class instead of a raw MagicMock.
class _BaseFileSystemEventHandler:
    """Minimal stand-in for watchdog.events.FileSystemEventHandler."""

_mock_events = mock.MagicMock()
_mock_events.FileSystemEventHandler = _BaseFileSystemEventHandler
sys.modules["watchdog.events"] = _mock_events

# ── Load panel-sync.py as the `panel_sync` module ────────────────────────────

_HERE = os.path.dirname(__file__)
_SCRIPT = os.path.abspath(os.path.join(_HERE, "..", "panel-sync.py"))

_spec = importlib.util.spec_from_file_location("panel_sync", _SCRIPT)
panel_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(panel_sync)

# Make it importable by name so test files can do `import panel_sync`
sys.modules["panel_sync"] = panel_sync


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings(monkeypatch):
    """Replace the global _cinnamon_settings singleton with a MagicMock."""
    settings = mock.MagicMock()
    monkeypatch.setattr(panel_sync, "_cinnamon_settings", settings)
    return settings


@pytest.fixture
def transparent_glib(monkeypatch):
    """Make GLib.Variant(type, value) simply return value for easier assertions."""
    monkeypatch.setattr(panel_sync.GLib, "Variant", lambda t, v: v)
