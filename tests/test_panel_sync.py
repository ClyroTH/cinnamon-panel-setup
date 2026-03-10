"""
Tests for panel-sync.py

Coverage areas:
  1. instance_file()                  — pure path construction
  2. get_instances_from_cinnamon()    — CSV parsing of eval_js result
  3. read_pinned()                    — JSON file reading
  4. get_monitor_count()              — /sys/class/drm file parsing
  5. get_panels_config()              — panels-enabled gsettings parsing
  6. eval_js()                        — dbus-send output parsing
  7. set_pinned()                     — eval_js result handling
  8. sync_to_all()                    — per-target dispatch
  9. _sync_zone_sizes()               — icon-size equalisation logic
 10. create_panels_for_new_monitors() — monitor hotplug response
 11. InstanceFileHandler              — debounce, routing, _process logic
 12. startup_sync()                   — retry loop and early-exit behaviour
"""
import json
import os
import subprocess
import unittest.mock as mock

import pytest

import panel_sync  # loaded by conftest.py


# ══════════════════════════════════════════════════════════════════════════════
# 1. instance_file
# ══════════════════════════════════════════════════════════════════════════════

class TestInstanceFile:
    def test_constructs_correct_path(self):
        path = panel_sync.instance_file("42")
        assert path == os.path.join(panel_sync.BASE, "42.json")

    def test_different_ids_give_different_paths(self):
        assert panel_sync.instance_file("1") != panel_sync.instance_file("2")

    def test_path_ends_with_json(self):
        assert panel_sync.instance_file("99").endswith(".json")


# ══════════════════════════════════════════════════════════════════════════════
# 2. get_instances_from_cinnamon  (only the CSV-parsing branch)
# ══════════════════════════════════════════════════════════════════════════════

class TestGetInstancesFromCinnamon:
    def test_normal_csv(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "1,2,3")
        assert panel_sync.get_instances_from_cinnamon() == ["1", "2", "3"]

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: " 1 , 2 ")
        assert panel_sync.get_instances_from_cinnamon() == ["1", "2"]

    def test_single_instance(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "5")
        assert panel_sync.get_instances_from_cinnamon() == ["5"]

    def test_empty_string_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "")
        assert panel_sync.get_instances_from_cinnamon() == []

    def test_only_commas_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: ",,")
        assert panel_sync.get_instances_from_cinnamon() == []


# ══════════════════════════════════════════════════════════════════════════════
# 3. read_pinned
# ══════════════════════════════════════════════════════════════════════════════

class TestReadPinned:
    def test_valid_json_returns_pinned_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        data = {"pinned-apps": {"value": ["firefox.desktop", "files.desktop"]}}
        (tmp_path / "1.json").write_text(json.dumps(data))
        assert panel_sync.read_pinned("1") == ["firefox.desktop", "files.desktop"]

    def test_missing_pinned_apps_key_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        (tmp_path / "1.json").write_text(json.dumps({}))
        assert panel_sync.read_pinned("1") == []

    def test_missing_value_key_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        (tmp_path / "1.json").write_text(json.dumps({"pinned-apps": {}}))
        assert panel_sync.read_pinned("1") == []

    def test_malformed_json_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        (tmp_path / "1.json").write_text("not json {{{")
        assert panel_sync.read_pinned("1") is None

    def test_file_not_found_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        assert panel_sync.read_pinned("nonexistent") is None

    def test_empty_pinned_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(panel_sync, "BASE", str(tmp_path))
        data = {"pinned-apps": {"value": []}}
        (tmp_path / "2.json").write_text(json.dumps(data))
        assert panel_sync.read_pinned("2") == []


# ══════════════════════════════════════════════════════════════════════════════
# 4. get_monitor_count
# ══════════════════════════════════════════════════════════════════════════════

class TestGetMonitorCount:
    def _fake_glob(self, monkeypatch, status_map: dict):
        """
        status_map: {filename: content}, e.g. {"card0-HDMI": "connected"}
        Creates real temp files and patches glob.glob to return their paths.
        """
        # We create temp files lazily using a closure over tmp_path.
        # Instead, return a helper that the caller can use.
        return status_map

    def test_two_connected_one_disconnected(self, tmp_path, monkeypatch):
        drm = tmp_path / "drm"
        for name, status in [("HDMI-1", "connected"), ("DP-1", "connected"), ("DP-2", "disconnected")]:
            d = drm / name
            d.mkdir(parents=True)
            (d / "status").write_text(status + "\n")

        paths = [str(drm / name / "status") for name in ("HDMI-1", "DP-1", "DP-2")]
        monkeypatch.setattr(panel_sync.glob, "glob", lambda p: paths)
        assert panel_sync.get_monitor_count() == 2

    def test_all_disconnected_returns_one(self, tmp_path, monkeypatch):
        drm = tmp_path / "drm" / "DP-1"
        drm.mkdir(parents=True)
        (drm / "status").write_text("disconnected\n")
        monkeypatch.setattr(panel_sync.glob, "glob", lambda p: [str(drm / "status")])
        assert panel_sync.get_monitor_count() == 1

    def test_no_drm_files_returns_one(self, monkeypatch):
        monkeypatch.setattr(panel_sync.glob, "glob", lambda p: [])
        assert panel_sync.get_monitor_count() == 1

    def test_glob_exception_returns_one(self, monkeypatch):
        def raise_oserror(p):
            raise OSError("no permission")
        monkeypatch.setattr(panel_sync.glob, "glob", raise_oserror)
        assert panel_sync.get_monitor_count() == 1

    def test_single_connected_returns_one(self, tmp_path, monkeypatch):
        drm = tmp_path / "drm" / "HDMI-1"
        drm.mkdir(parents=True)
        (drm / "status").write_text("connected")
        monkeypatch.setattr(panel_sync.glob, "glob", lambda p: [str(drm / "status")])
        assert panel_sync.get_monitor_count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. get_panels_config
# ══════════════════════════════════════════════════════════════════════════════

class TestGetPanelsConfig:
    def _patch_settings(self, monkeypatch, panels_list):
        settings = mock.MagicMock()
        settings.get_value.return_value = panels_list
        monkeypatch.setattr(panel_sync, "_cinnamon_settings", settings)

    def test_normal_entries(self, monkeypatch):
        self._patch_settings(monkeypatch, ["1:0:bottom", "2:1:bottom"])
        assert panel_sync.get_panels_config() == [(1, 0, "bottom"), (2, 1, "bottom")]

    def test_malformed_entry_is_skipped(self, monkeypatch):
        self._patch_settings(monkeypatch, ["1:0:bottom", "bad-entry", "2:1:top"])
        result = panel_sync.get_panels_config()
        assert result == [(1, 0, "bottom"), (2, 1, "top")]

    def test_empty_list(self, monkeypatch):
        self._patch_settings(monkeypatch, [])
        assert panel_sync.get_panels_config() == []

    def test_position_string_preserved(self, monkeypatch):
        self._patch_settings(monkeypatch, ["3:2:top"])
        assert panel_sync.get_panels_config() == [(3, 2, "top")]

    def test_ids_are_integers(self, monkeypatch):
        self._patch_settings(monkeypatch, ["10:0:bottom"])
        result = panel_sync.get_panels_config()
        pid, mon, _ = result[0]
        assert isinstance(pid, int)
        assert isinstance(mon, int)


# ══════════════════════════════════════════════════════════════════════════════
# 6. eval_js
# ══════════════════════════════════════════════════════════════════════════════

class TestEvalJs:
    def _patch_run(self, monkeypatch, stdout="", exception=None):
        def fake_run(*args, **kwargs):
            if exception:
                raise exception
            result = mock.MagicMock()
            result.stdout = stdout
            return result
        monkeypatch.setattr(panel_sync.subprocess, "run", fake_run)

    def test_ok_result_parsed(self, monkeypatch):
        self._patch_run(monkeypatch, stdout='   string "OK"\n')
        assert panel_sync.eval_js("js") == "OK"

    def test_err_result_parsed(self, monkeypatch):
        self._patch_run(monkeypatch, stdout='   string "ERR:something went wrong"\n')
        assert panel_sync.eval_js("js") == "ERR:something went wrong"

    def test_no_string_line_returns_empty(self, monkeypatch):
        self._patch_run(monkeypatch, stdout="method return time=1234\n   uint32 1\n")
        assert panel_sync.eval_js("js") == ""

    def test_timeout_returns_empty(self, monkeypatch):
        self._patch_run(monkeypatch, exception=subprocess.TimeoutExpired("dbus-send", 5))
        assert panel_sync.eval_js("js") == ""

    def test_file_not_found_returns_empty(self, monkeypatch):
        self._patch_run(monkeypatch, exception=FileNotFoundError("dbus-send not found"))
        assert panel_sync.eval_js("js") == ""

    def test_arbitrary_exception_returns_empty(self, monkeypatch):
        self._patch_run(monkeypatch, exception=RuntimeError("unexpected"))
        assert panel_sync.eval_js("js") == ""


# ══════════════════════════════════════════════════════════════════════════════
# 7. set_pinned
# ══════════════════════════════════════════════════════════════════════════════

class TestSetPinned:
    def test_ok_returns_true(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "OK")
        assert panel_sync.set_pinned("1", ["app.desktop"]) is True

    def test_err_returns_false(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "ERR:no settings for 1")
        assert panel_sync.set_pinned("1", []) is False

    def test_empty_string_returns_false(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: "")
        assert panel_sync.set_pinned("1", []) is False

    def test_pinned_list_is_json_encoded_in_js(self, monkeypatch):
        """Verify the JS snippet contains the serialised pinned list."""
        captured = []
        monkeypatch.setattr(panel_sync, "eval_js", lambda js: captured.append(js) or "OK")
        panel_sync.set_pinned("7", ["a.desktop", "b.desktop"])
        assert '["a.desktop", "b.desktop"]' in captured[0]


# ══════════════════════════════════════════════════════════════════════════════
# 8. sync_to_all
# ══════════════════════════════════════════════════════════════════════════════

class TestSyncToAll:
    def test_calls_set_pinned_for_each_target(self, monkeypatch):
        calls = []
        monkeypatch.setattr(panel_sync, "set_pinned", lambda tid, p: calls.append(tid) or True)
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: None)
        panel_sync.sync_to_all("1", ["app.desktop"], ["2", "3"])
        assert calls == ["2", "3"]

    def test_continues_after_failed_target(self, monkeypatch):
        calls = []

        def fake_set_pinned(tid, p):
            calls.append(tid)
            return tid != "2"  # instance 2 fails

        monkeypatch.setattr(panel_sync, "set_pinned", fake_set_pinned)
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: None)
        panel_sync.sync_to_all("1", [], ["2", "3"])
        assert "2" in calls and "3" in calls

    def test_empty_targets_does_nothing(self, monkeypatch):
        calls = []
        monkeypatch.setattr(panel_sync, "set_pinned", lambda tid, p: calls.append(tid) or True)
        panel_sync.sync_to_all("1", [], [])
        assert calls == []

    def test_sleeps_between_successful_updates(self, monkeypatch):
        sleep_calls = []
        monkeypatch.setattr(panel_sync, "set_pinned", lambda tid, p: True)
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: sleep_calls.append(s))
        panel_sync.sync_to_all("1", [], ["2", "3"])
        assert len(sleep_calls) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 9. _sync_zone_sizes
# ══════════════════════════════════════════════════════════════════════════════

class TestSyncZoneSizes:
    """
    _sync_zone_sizes(key) uses _settings() for two things:
      - .get_value(key).unpack() → JSON string of zone-size dicts
      - .get_value("panels-enabled") → iterable of "pid:mon:pos" strings  (via gsettings_get_list)
    """

    KEY = "panel-zone-icon-sizes"

    def _make_settings(self, monkeypatch, zone_data, panels_enabled):
        zone_json = json.dumps(zone_data)

        def get_value(key):
            if key == self.KEY:
                m = mock.MagicMock()
                m.unpack.return_value = zone_json
                return m
            # panels-enabled — must be iterable for list()
            return panels_enabled

        settings = mock.MagicMock()
        settings.get_value.side_effect = get_value
        monkeypatch.setattr(panel_sync, "_cinnamon_settings", settings)
        return settings

    def test_all_equal_no_write(self, monkeypatch):
        zone = [
            {"panelId": 1, "left": 22, "center": 22, "right": 16},
            {"panelId": 2, "left": 22, "center": 22, "right": 16},
        ]
        settings = self._make_settings(monkeypatch, zone, ["1:0:bottom", "2:1:bottom"])
        result = panel_sync._sync_zone_sizes(self.KEY)
        settings.set_value.assert_not_called()
        assert result == 16

    def test_mismatched_panel_triggers_write(self, monkeypatch):
        zone = [
            {"panelId": 1, "left": 22, "center": 22, "right": 16},
            {"panelId": 2, "left": 24, "center": 24, "right": 20},
        ]
        settings = self._make_settings(monkeypatch, zone, ["1:0:bottom", "2:1:bottom"])
        panel_sync._sync_zone_sizes(self.KEY)
        settings.set_value.assert_called_once()

    def test_missing_panel_triggers_write(self, monkeypatch):
        """Panel listed in panels-enabled but absent from zone data → added."""
        zone = [{"panelId": 1, "left": 22, "center": 22, "right": 16}]
        settings = self._make_settings(monkeypatch, zone, ["1:0:bottom", "2:1:bottom"])
        panel_sync._sync_zone_sizes(self.KEY)
        settings.set_value.assert_called_once()

    def test_no_panel1_uses_first_entry_as_ref(self, monkeypatch):
        zone = [
            {"panelId": 3, "left": 22, "center": 22, "right": 14},
            {"panelId": 4, "left": 22, "center": 22, "right": 14},
        ]
        settings = self._make_settings(monkeypatch, zone, ["3:0:bottom", "4:1:bottom"])
        result = panel_sync._sync_zone_sizes(self.KEY)
        assert result == 14  # right value from first entry (panelId=3)

    def test_empty_zone_data_returns_zero(self, monkeypatch):
        settings = self._make_settings(monkeypatch, [], ["1:0:bottom"])
        result = panel_sync._sync_zone_sizes(self.KEY)
        assert result == 0

    def test_json_parse_error_returns_zero(self, monkeypatch):
        settings = mock.MagicMock()
        bad = mock.MagicMock()
        bad.unpack.return_value = "not json {{{"
        settings.get_value.return_value = bad
        monkeypatch.setattr(panel_sync, "_cinnamon_settings", settings)
        result = panel_sync._sync_zone_sizes(self.KEY)
        assert result == 0

    def test_returns_right_size_of_ref_panel(self, monkeypatch, transparent_glib):
        zone = [{"panelId": 1, "left": 22, "center": 22, "right": 24}]
        settings = self._make_settings(monkeypatch, zone, ["1:0:bottom"])
        result = panel_sync._sync_zone_sizes(self.KEY)
        assert result == 24

    def test_written_value_equalises_panel2_to_panel1(self, monkeypatch, transparent_glib):
        """With transparent_glib, we can inspect the value written to gsettings."""
        zone = [
            {"panelId": 1, "left": 22, "center": 22, "right": 16},
            {"panelId": 2, "left": 99, "center": 99, "right": 99},
        ]
        settings = self._make_settings(monkeypatch, zone, ["1:0:bottom", "2:1:bottom"])
        panel_sync._sync_zone_sizes(self.KEY)
        written_arg = settings.set_value.call_args[0][1]  # GLib.Variant is transparent → list
        new_data = json.loads(written_arg[0])
        panel2 = next(p for p in new_data if p["panelId"] == 2)
        assert panel2["right"] == 16
        assert panel2["left"] == 22


# ══════════════════════════════════════════════════════════════════════════════
# 10. create_panels_for_new_monitors
# ══════════════════════════════════════════════════════════════════════════════

class TestCreatePanelsForNewMonitors:
    def _setup(self, monkeypatch, monitor_count, panels, heights=None):
        monkeypatch.setattr(panel_sync, "get_monitor_count", lambda: monitor_count)
        monkeypatch.setattr(panel_sync, "get_panels_config", lambda: panels)
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: None)

        heights_list = heights or [f"{p[0]}:40" for p in panels]

        settings = mock.MagicMock()
        settings.get_value.return_value = heights_list
        monkeypatch.setattr(panel_sync, "_cinnamon_settings", settings)
        return settings

    def test_no_new_monitor_returns_false(self, monkeypatch):
        panels = [(1, 0, "bottom"), (2, 1, "bottom")]
        self._setup(monkeypatch, monitor_count=2, panels=panels)
        assert panel_sync.create_panels_for_new_monitors("1") is False

    def test_new_monitor_returns_true(self, monkeypatch, transparent_glib):
        panels = [(1, 0, "bottom")]
        settings = self._setup(monkeypatch, monitor_count=2, panels=panels)
        result = panel_sync.create_panels_for_new_monitors("1")
        assert result is True
        assert settings.set_value.call_count >= 2  # panels-enabled + panels-height

    def test_no_ref_panel_returns_false(self, monkeypatch):
        """If no panel is on monitor 0, we cannot use it as a reference."""
        panels = [(2, 1, "bottom")]  # only monitor 1 panel, no monitor 0
        self._setup(monkeypatch, monitor_count=2, panels=panels)
        assert panel_sync.create_panels_for_new_monitors("1") is False

    def test_new_panel_gets_next_id(self, monkeypatch, transparent_glib):
        """New panel ID should be max(existing) + 1."""
        panels = [(3, 0, "bottom")]
        settings = self._setup(monkeypatch, monitor_count=2, panels=panels)
        panel_sync.create_panels_for_new_monitors("3")
        # The panels-enabled write should include panel id 4
        written = settings.set_value.call_args_list
        panels_enabled_call = next(
            c for c in written if c[0][0] == "panels-enabled"
        )
        value = panels_enabled_call[0][1]
        assert any("4:" in v for v in value)


# ══════════════════════════════════════════════════════════════════════════════
# 11. InstanceFileHandler
# ══════════════════════════════════════════════════════════════════════════════

class TestInstanceFileHandler:
    def _make_handler(self, instances):
        return panel_sync.InstanceFileHandler([list(instances)])

    def _make_event(self, path, is_directory=False):
        e = mock.MagicMock()
        e.src_path = path
        e.dest_path = path
        e.is_directory = is_directory
        return e

    # ── _handle filtering ─────────────────────────────────────────────────────

    def test_non_json_path_ignored(self, monkeypatch):
        handler = self._make_handler(["1", "2"])
        with mock.patch.object(handler, "_process") as mock_proc:
            handler._handle("/some/path/file.txt")
            mock_proc.assert_not_called()

    def test_unknown_instance_ignored(self, monkeypatch):
        handler = self._make_handler(["1", "2"])
        with mock.patch.object(handler, "_process") as mock_proc:
            handler._handle("/some/path/99.json")
            mock_proc.assert_not_called()

    def test_known_instance_creates_timer(self, monkeypatch):
        mock_timer = mock.MagicMock()
        monkeypatch.setattr(panel_sync.threading, "Timer",
                            mock.MagicMock(return_value=mock_timer))
        handler = self._make_handler(["1", "2"])
        handler._handle("/some/path/1.json")
        assert mock_timer.start.call_count == 1

    def test_debounce_cancels_previous_timer(self, monkeypatch):
        timer1, timer2 = mock.MagicMock(), mock.MagicMock()
        monkeypatch.setattr(panel_sync.threading, "Timer",
                            mock.MagicMock(side_effect=[timer1, timer2]))
        handler = self._make_handler(["1"])
        handler._handle("/path/1.json")
        handler._handle("/path/1.json")  # second call within debounce window
        timer1.cancel.assert_called_once()
        timer2.start.assert_called_once()

    # ── on_modified / on_created / on_moved routing ───────────────────────────

    def test_on_modified_file_event_calls_handle(self, monkeypatch):
        handler = self._make_handler(["1"])
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_modified(self._make_event("/path/1.json", is_directory=False))
            mock_handle.assert_called_once_with("/path/1.json")

    def test_on_modified_directory_event_ignored(self, monkeypatch):
        handler = self._make_handler(["1"])
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_modified(self._make_event("/path/1.json", is_directory=True))
            mock_handle.assert_not_called()

    def test_on_created_file_event_calls_handle(self):
        handler = self._make_handler(["1"])
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_created(self._make_event("/path/1.json", is_directory=False))
            mock_handle.assert_called_once()

    def test_on_created_directory_event_ignored(self):
        handler = self._make_handler(["1"])
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_created(self._make_event("/path/1.json", is_directory=True))
            mock_handle.assert_not_called()

    def test_on_moved_uses_dest_path(self):
        handler = self._make_handler(["1"])
        event = mock.MagicMock()
        event.is_directory = False
        event.dest_path = "/path/1.json"
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_moved(event)
            mock_handle.assert_called_once_with("/path/1.json")

    def test_on_moved_directory_event_ignored(self):
        handler = self._make_handler(["1"])
        event = mock.MagicMock()
        event.is_directory = True
        event.dest_path = "/path/1.json"
        with mock.patch.object(handler, "_handle") as mock_handle:
            handler.on_moved(event)
            mock_handle.assert_not_called()

    # ── _process logic ────────────────────────────────────────────────────────

    def test_process_skips_sync_when_all_targets_match(self, monkeypatch):
        pinned = ["app.desktop"]
        monkeypatch.setattr(panel_sync, "read_pinned", lambda iid: pinned)
        monkeypatch.setattr(panel_sync, "sync_to_all", mock.MagicMock())
        handler = self._make_handler(["1", "2"])
        handler._process("1")
        panel_sync.sync_to_all.assert_not_called()

    def test_process_syncs_when_target_differs(self, monkeypatch):
        def fake_read(iid):
            return ["app1.desktop"] if iid == "1" else ["app2.desktop"]

        monkeypatch.setattr(panel_sync, "read_pinned", fake_read)
        mock_sync = mock.MagicMock()
        monkeypatch.setattr(panel_sync, "sync_to_all", mock_sync)
        handler = self._make_handler(["1", "2"])
        handler._process("1")
        mock_sync.assert_called_once()

    def test_process_returns_early_when_read_pinned_is_none(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "read_pinned", lambda iid: None)
        mock_sync = mock.MagicMock()
        monkeypatch.setattr(panel_sync, "sync_to_all", mock_sync)
        handler = self._make_handler(["1", "2"])
        handler._process("1")
        mock_sync.assert_not_called()

    def test_process_with_no_targets_does_not_sync(self, monkeypatch):
        """Only one instance → no targets → sync_to_all should not be called."""
        monkeypatch.setattr(panel_sync, "read_pinned", lambda iid: ["app.desktop"])
        mock_sync = mock.MagicMock()
        monkeypatch.setattr(panel_sync, "sync_to_all", mock_sync)
        handler = self._make_handler(["1"])  # only source, no targets
        handler._process("1")
        mock_sync.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 12. startup_sync
# ══════════════════════════════════════════════════════════════════════════════

class TestStartupSync:
    def _common_patches(self, monkeypatch, get_instances_fn):
        monkeypatch.setattr(panel_sync, "get_instances_from_cinnamon", get_instances_fn)
        monkeypatch.setattr(panel_sync, "sync_icon_sizes", lambda: None)
        monkeypatch.setattr(panel_sync, "create_panels_for_new_monitors", lambda sid: False)
        monkeypatch.setattr(panel_sync, "read_pinned", lambda iid: ["app.desktop"])
        monkeypatch.setattr(panel_sync, "set_pinned", lambda iid, p: True)
        monkeypatch.setattr(panel_sync, "sync_to_all", lambda *a: None)
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: None)

    def test_returns_instances_on_first_attempt(self, monkeypatch):
        self._common_patches(monkeypatch, lambda: ["1", "2"])
        result = panel_sync.startup_sync()
        assert result == ["1", "2"]

    def test_retries_until_instances_found(self, monkeypatch):
        call_count = [0]

        def fake_get():
            call_count[0] += 1
            return [] if call_count[0] < 3 else ["5"]

        self._common_patches(monkeypatch, fake_get)
        result = panel_sync.startup_sync()
        assert result == ["5"]
        assert call_count[0] >= 3  # two failed + one success in retry loop

    def test_returns_none_after_all_retries_fail(self, monkeypatch):
        monkeypatch.setattr(panel_sync, "get_instances_from_cinnamon", lambda: [])
        monkeypatch.setattr(panel_sync.time, "sleep", lambda s: None)
        result = panel_sync.startup_sync()
        assert result is None

    def test_returns_none_when_read_pinned_fails(self, monkeypatch):
        """If the source pinned file is unreadable, startup_sync exits early."""
        self._common_patches(monkeypatch, lambda: ["1", "2"])
        monkeypatch.setattr(panel_sync, "read_pinned", lambda iid: None)
        result = panel_sync.startup_sync()
        assert result is None

    def test_source_is_instance_with_lowest_id(self, monkeypatch):
        """The instance with the numerically smallest ID should be chosen as source."""
        set_calls = []

        def fake_set_pinned(iid, p):
            set_calls.append(iid)
            return True

        self._common_patches(monkeypatch, lambda: ["3", "1", "2"])
        monkeypatch.setattr(panel_sync, "set_pinned", fake_set_pinned)
        panel_sync.startup_sync()
        # set_pinned is called first on the source
        assert set_calls[0] == "1"
