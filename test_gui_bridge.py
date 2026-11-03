# SPDX-License-Identifier: GPL-3.0-only
"""The GUI may write preferences; it must never invent backend success."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from build_click import stage_app
from slider_common import (APP_ID, VERSION, atomic_json_write, default_config,
                           load_config, save_config)

SOURCE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("gui_bridge", SOURCE / "app/python/gui_bridge.py")
gui_bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gui_bridge)


class GuiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.now = 100.0
        self.bridge = gui_bridge.Bridge(self.home, clock=lambda: self.now)

    def report(self, **changes):
        config = self.bridge.snapshot()["config"]
        report = {"schema_version": 1, "protocol_version": 1, "backend_version": VERSION,
                  "instance_id": "watcher-instance", "heartbeat_monotonic": self.now,
                  "observed_revision": config["revision"], "applied_revision": config["revision"],
                  "slider": "open", "state": "active", "config": config, "config_error": None,
                  "capabilities": {name: True for name in gui_bridge.FEATURES},
                  "features": {name: {"state": "active", "message": ""} for name in gui_bridge.FEATURES},
                  "platform": {"overlay": "mounted", "reload_required": None}}
        report.update(changes)
        atomic_json_write(self.bridge.status_file, report)
        return report

    def test_snapshot_is_read_only_and_stable_without_watcher_or_preferences(self):
        first = self.bridge.snapshot()
        self.assertEqual(first["backend"], "missing")
        self.assertIsNone(first["status"])
        self.assertEqual(first["config"], self.bridge.snapshot()["config"])
        self.assertFalse(self.bridge.preferences.exists())
        self.assertFalse(self.bridge.status_file.exists())

    def test_patch_preserves_independent_choices_and_writes_new_revision(self):
        original = default_config()
        original["rotation"]["enabled"] = False
        save_config(self.bridge.preferences, original)
        patch = {"backlight": {"brightness_percent": 0}, "keyboard": {"hide_when_open": False}}
        unchanged = copy.deepcopy(patch)
        result = self.bridge.save_patch(patch)
        self.assertIsNone(result["error"])
        saved = load_config(self.bridge.preferences)
        self.assertEqual(saved, result["config"])
        self.assertEqual(saved["backlight"], {"enabled": True, "brightness_percent": 0})
        self.assertFalse(saved["rotation"]["enabled"])
        self.assertFalse(saved["keyboard"]["hide_when_open"])
        self.assertNotEqual(saved["revision"], original["revision"])
        self.assertEqual(patch, unchanged)
        second = self.bridge.save_patch({"enabled": False})
        self.assertNotEqual(second["config"]["revision"], saved["revision"])
        self.assertEqual(second["config"]["rotation"], saved["rotation"])

    def test_invalid_or_privileged_fields_never_change_the_preferences_file(self):
        save_config(self.bridge.preferences, default_config())
        before = self.bridge.preferences.read_bytes()
        invalid = [None, {}, {"schema_version": 2}, {"revision": "chosen"},
                   {"command": "reboot"}, {"config_path": "/tmp/other"},
                   {"enabled": 1}, {"backlight": {}}, {"backlight": {"path": "/sys/led"}},
                   {"backlight": {"brightness_percent": True}},
                   {"backlight": {"brightness_percent": -1}},
                   {"backlight": {"brightness_percent": 101}},
                   {"backlight": {"brightness_percent": 10.5}},
                   {"backlight": {"brightness_percent": float("nan")}},
                   {"backlight": {"brightness_percent": float("inf")}},
                   {"rotation": {"direction": "automatic"}},
                   {"keyboard": {"hide_when_open": "yes"}}]
        for patch in invalid:
            with self.subTest(patch=patch):
                result = self.bridge.save_patch(patch)
                self.assertIn("not saved", result["error"])
                self.assertEqual(self.bridge.preferences.read_bytes(), before)

    def test_whole_brightness_double_from_qml_is_saved_as_a_strict_integer(self):
        original = default_config()
        original["rotation"]["direction"] = "reversed"
        save_config(self.bridge.preferences, original)
        patch = {"backlight": {"brightness_percent": 38.0}}
        result = self.bridge.save_patch(patch)
        self.assertIsNone(result["error"])
        self.assertEqual(result["config"]["backlight"]["brightness_percent"], 38)
        self.assertIs(type(result["config"]["backlight"]["brightness_percent"]), int)
        self.assertEqual(load_config(self.bridge.preferences), result["config"])
        self.assertEqual(result["config"]["rotation"]["direction"], "reversed")
        self.assertIs(type(patch["backlight"]["brightness_percent"]), float)

    def test_bad_config_is_reported_and_only_explicit_defaults_repair_it(self):
        save_config(self.bridge.preferences, default_config())
        saved = self.bridge.save_patch({"backlight": {"brightness_percent": 25}})["config"]
        self.bridge.preferences.write_text('{"schema_version": 1, "enabled": true}')
        result = self.bridge.snapshot()
        self.assertIn("Could not read saved settings", result["error"])
        self.assertEqual(result["config"], saved)
        before = self.bridge.preferences.read_bytes()
        self.assertIn("not saved", self.bridge.save_patch({"enabled": False})["error"])
        self.assertEqual(self.bridge.preferences.read_bytes(), before)
        result = self.bridge.reset_defaults()
        self.assertIsNone(result["error"])
        self.assertEqual(result["config"]["backlight"]["brightness_percent"], 100)
        self.assertNotEqual(result["config"]["revision"], saved["revision"])

    def test_save_failure_is_visible_and_preserves_existing_choices(self):
        save_config(self.bridge.preferences, default_config())
        self.bridge.save_patch({"enabled": False})
        before = self.bridge.preferences.read_bytes()
        with mock.patch.object(gui_bridge, "save_config", side_effect=OSError("read only")):
            result = self.bridge.save_patch({"enabled": True})
        self.assertIn("read only", result["error"])
        self.assertFalse(result["config"]["enabled"])
        self.assertEqual(self.bridge.preferences.read_bytes(), before)

    def test_fresh_recovered_preferences_are_preserved_by_a_partial_save(self):
        recovered = default_config()
        recovered["backlight"]["brightness_percent"] = 25
        recovered["rotation"]["direction"] = "reversed"
        report_fields = {"config": recovered, "observed_revision": recovered["revision"],
                         "applied_revision": recovered["revision"]}
        self.report(**report_fields)
        self.assertEqual(self.bridge.snapshot()["backend"], "checking")
        refused = self.bridge.save_patch({"keyboard": {"hide_when_open": False}})
        self.assertIn("fresh watcher configuration", refused["error"])
        self.assertFalse(self.bridge.preferences.exists())
        self.now += 2
        self.report(**report_fields)
        verified = self.bridge.snapshot()
        self.assertEqual(verified["backend"], "running")
        self.assertEqual(verified["config"], recovered)
        result = self.bridge.save_patch({"keyboard": {"hide_when_open": False}})
        self.assertIsNone(result["error"])
        self.assertEqual(result["config"]["backlight"]["brightness_percent"], 25)
        self.assertEqual(result["config"]["rotation"]["direction"], "reversed")
        self.assertFalse(result["config"]["keyboard"]["hide_when_open"])

    def test_stale_recovery_report_cannot_seed_a_missing_preference_file(self):
        recovered = default_config()
        recovered["backlight"]["brightness_percent"] = 25
        self.report(config=recovered, observed_revision=recovered["revision"],
                    applied_revision=recovered["revision"], heartbeat_monotonic=self.now - 50)
        result = self.bridge.save_patch({"keyboard": {"hide_when_open": False}})
        self.assertEqual(result["backend"], "stale")
        self.assertIn("fresh watcher configuration", result["error"])
        self.assertEqual(result["config"]["backlight"]["brightness_percent"], 100)
        self.assertFalse(self.bridge.preferences.exists())
        # Defaults are an explicit complete replacement and can start offline.
        self.assertIsNone(self.bridge.reset_defaults()["error"])

    def test_incompatible_backend_blocks_both_partial_saves_and_defaults(self):
        save_config(self.bridge.preferences, default_config())
        self.report(protocol_version=2)
        before = self.bridge.preferences.read_bytes()
        for action in (lambda: self.bridge.save_patch({"enabled": False}), self.bridge.reset_defaults):
            result = action()
            self.assertEqual(result["backend"], "incompatible")
            self.assertIn("incompatible", result["error"])
            self.assertEqual(self.bridge.preferences.read_bytes(), before)

    def test_valid_preferences_can_be_saved_offline_without_an_applied_ack(self):
        save_config(self.bridge.preferences, default_config())
        result = self.bridge.save_patch({"enabled": False})
        self.assertIsNone(result["error"])
        self.assertFalse(result["config"]["enabled"])
        self.assertEqual(result["backend"], "missing")
        self.assertIsNone(result["status"])

    def test_cached_status_requires_a_new_heartbeat_on_launch_and_resume(self):
        self.report()
        self.assertEqual(self.bridge.snapshot()["backend"], "checking")
        self.assertEqual(self.bridge.snapshot()["backend"], "checking")
        self.now += 2
        self.report()
        self.assertEqual(self.bridge.snapshot()["backend"], "running")
        self.assertEqual(self.bridge.resume()["backend"], "checking")
        self.now += 2
        self.report()
        self.assertEqual(self.bridge.snapshot()["backend"], "running")
        self.now += gui_bridge.STALE_AFTER_SECONDS + 1
        self.assertEqual(self.bridge.snapshot()["backend"], "stale")
        self.report()
        self.assertEqual(self.bridge.snapshot()["backend"], "running")

    def test_old_boot_future_and_missing_status_are_never_running(self):
        self.report(heartbeat_monotonic=self.now + 50)
        self.assertEqual(self.bridge.snapshot()["backend"], "stale")
        self.report(heartbeat_monotonic=self.now - 50)
        self.assertEqual(self.bridge.snapshot()["backend"], "stale")
        self.bridge.status_file.unlink()
        self.assertEqual(self.bridge.snapshot()["backend"], "missing")

    def test_pending_and_degraded_status_does_not_prevent_saving(self):
        self.report(state="degraded", applied_revision=None)
        self.bridge.snapshot()
        self.now += 2
        old_report = self.report(state="degraded", applied_revision=None)
        result = self.bridge.save_patch({"enabled": False})
        self.assertEqual(result["backend"], "running")
        self.assertIsNone(result["error"])
        self.assertNotEqual(result["config"]["revision"], old_report["observed_revision"])
        self.assertEqual(result["status"]["observed_revision"], old_report["observed_revision"])
        self.assertIsNone(result["status"]["applied_revision"])

    def test_malformed_incompatible_and_oversized_reports_are_not_trusted(self):
        valid = self.report()
        invalid = [dict(valid, protocol_version=2), dict(valid, schema_version=True),
                   dict(valid, heartbeat_monotonic=True), dict(valid, heartbeat_monotonic=float("inf")),
                   dict(valid, applied_revision="unobserved"), dict(valid, config={}),
                   dict(valid, features={}), dict(valid, capabilities={}),
                   dict(valid, platform={"overlay": "mounted"})]
        incomplete = dict(valid)
        del incomplete["applied_revision"]
        invalid.append(incomplete)
        for report in invalid:
            with self.subTest(report=report):
                self.bridge.status_file.write_text(json.dumps(report))
                result = self.bridge.snapshot()
                self.assertEqual(result["backend"], "incompatible")
                self.assertIsNone(result["status"])
                self.assertTrue(result["error"])
        for content in ("{" + '"state":"active",' * 2 + '"x":1}', " " * 16385, "{bad", "[]"):
            with self.subTest(content=content[:60]):
                self.bridge.status_file.write_text(content)
                self.assertEqual(self.bridge.snapshot()["backend"], "incompatible")

    def test_optional_dimming_capability_is_strict_and_missing_defaults_to_false(self):
        self.report()
        result = self.bridge.snapshot()
        self.assertFalse(result["status"]["capabilities"]["backlight_dimming"])
        self.assertFalse(result["dimming_known"])
        for supported in (False, True):
            capabilities = {name: True for name in gui_bridge.FEATURES}
            capabilities["backlight_dimming"] = supported
            self.report(capabilities=capabilities)
            result = self.bridge.snapshot()
            self.assertIs(result["status"]["capabilities"]["backlight_dimming"], supported)
            self.assertTrue(result["dimming_known"])
        for malformed in (1, 0, None, "true", []):
            capabilities = {name: True for name in gui_bridge.FEATURES}
            capabilities["backlight_dimming"] = malformed
            self.report(capabilities=capabilities)
            result = self.bridge.snapshot()
            self.assertEqual(result["backend"], "incompatible")
            self.assertIsNone(result["status"])

    def test_probe_uses_both_app_scoped_directories_without_touching_settings(self):
        config_base, cache_base = self.home / ".config", self.home / ".cache"
        original_open, original_read = Path.open, Path.read_text
        outside = cache_base / "pro1x-slider-probe-outside.json"

        def confined_open(path, *args, **kwargs):
            if path == outside:
                raise PermissionError("AppArmor denied outside write")
            return original_open(path, *args, **kwargs)

        def label_read(path, *args, **kwargs):
            if str(path) == "/proc/self/attr/current":
                return APP_ID + "_slider-settings_0.3.0 (enforce)\n"
            return original_read(path, *args, **kwargs)

        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_base), "XDG_CACHE_HOME": str(cache_base)}), \
                mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.object(Path, "open", new=confined_open), \
                mock.patch.object(Path, "read_text", new=label_read):
            preferences = config_base / APP_ID / "config.json"
            status_file = cache_base / APP_ID / "status.json"
            save_config(preferences, default_config())
            atomic_json_write(status_file, {"marker": "keep"})
            before = preferences.read_bytes(), status_file.read_bytes()
            result = gui_bridge.probe()
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["outside_write_denied"])
            self.assertIn("(enforce)", result["apparmor_label"])
            self.assertEqual(json.loads(Path(result["result_path"]).read_text()), result)
            self.assertEqual(preferences.read_bytes(), before[0])
            self.assertEqual(status_file.read_bytes(), before[1])
            for path in result["paths"]:
                self.assertEqual(Path(path).parent.name, APP_ID)
                self.assertFalse(Path(path).exists())

    def test_probe_fails_if_outside_write_is_allowed_and_removes_its_scratch(self):
        config_base, cache_base = self.home / ".config", self.home / ".cache"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_base), "XDG_CACHE_HOME": str(cache_base)}), \
                mock.patch.object(Path, "home", return_value=self.home):
            result = gui_bridge.probe()
        self.assertFalse(result["ok"])
        self.assertFalse(result["outside_write_denied"])
        self.assertIn("outside", result["error"])
        self.assertFalse((cache_base / "pro1x-slider-probe-outside.json").exists())
        self.assertEqual(json.loads(Path(result["result_path"]).read_text()), result)
        self.assertFalse((config_base / APP_ID / "config.json").exists())
        self.assertFalse((cache_base / APP_ID / "status.json").exists())

    def test_stage_has_confined_native_entrypoint_and_its_own_protocol_copy(self):
        staged = stage_app(SOURCE, self.home / "click-source")
        self.assertEqual((staged / "python/slider_common.py").read_bytes(), (SOURCE / "slider_common.py").read_bytes())
        manifest = json.loads((staged / "manifest.json").read_text())
        self.assertEqual((manifest["name"], manifest["version"], manifest["architecture"]), (APP_ID, VERSION, "all"))
        policy = json.loads((staged / "slider-settings.apparmor").read_text())
        self.assertEqual(policy["policy_groups"], [])
        self.assertNotEqual(policy.get("template"), "unconfined")
        result = subprocess.run([sys.executable, "-I", "-c",
                                 "import sys;sys.path.insert(0,sys.argv[1]);import gui_bridge,slider_common;"
                                 "print(slider_common.__file__)", str(staged / "python")],
                                cwd=self.home, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(staged / "python"), result.stdout)
        marker = staged / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            stage_app(SOURCE, staged)
        self.assertEqual(marker.read_text(), "keep")

    @unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the QML display logic")
    def test_display_never_claims_applied_for_pending_stale_or_degraded_reports(self):
        script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const context = {}; vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const s = {config: {revision: 'new', enabled: true, backlight:{enabled:true}, rotation:{enabled:true}, keyboard:{hide_when_open:true}}, backend: 'running', error: null,
  status: {observed_revision: 'old', applied_revision: 'old', state: 'active', capabilities:{backlight:true, rotation:true, keyboard:true}, features:{rotation:{state:'idle',message:''}}}};
assert.match(context.heading(s, false, false), /waiting/);
s.status.observed_revision = 'new'; s.status.applied_revision = null;
assert.match(context.heading(s, false, false), /applying/);
s.status.state = 'degraded'; assert.match(context.heading(s, false, false), /attention/);
s.status.state = 'active'; s.status.applied_revision = 'new';
assert.strictEqual(context.heading(s, false, false), 'Settings applied');
s.status.capabilities.rotation = false;
assert.match(context.heading(s, false, false), /unavailable/);
assert.match(context.feature(s, 'rotation'), /unavailable/);
s.status.capabilities.rotation = true;
s.backend = 'stale'; assert.doesNotMatch(context.heading(s, false, false), /applied/);
s.backend = 'checking'; assert.doesNotMatch(context.heading(s, false, false), /applied/);
s.backend = 'running'; s.config.enabled = false;
assert.match(context.heading(s, false, false), /overrides restored/);
assert.strictEqual(context.canDim(s), false);
assert.match(context.dimmingDetail(s), /not reported/);
s.dimming_known = true; s.status.capabilities.backlight_dimming = false;
assert.strictEqual(context.canDim(s), false);
assert.match(context.dimmingDetail(s), /on\/off only/);
s.status.capabilities.backlight_dimming = true;
assert.strictEqual(context.canDim(s), true);
for (const backend of ['checking', 'stale', 'missing', 'incompatible']) {
  s.backend = backend;
  assert.strictEqual(context.canDim(s), false);
  assert.match(context.dimmingDetail(s), /not confirmed/);
}
"""
        result = subprocess.run(["node", "-e", script, str(SOURCE / "app/qml/Status.js")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
