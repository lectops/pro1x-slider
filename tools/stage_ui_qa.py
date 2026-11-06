#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage an isolated native-render QA harness; optionally run it over SSH.

The default bridge is a memory-only fixture. --bridge-check uses the production
bridge with throwaway XDG storage. No real configuration/status, hardware,
settings service, Click package, or AppArmor profile is changed.
"""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from build_click import stage_app


FIXTURE_BRIDGE = '''# Staging-only memory fixture: no file or device I/O.
import copy
import time

_config = {"schema_version": 2, "revision": "visual-qa", "enabled": True,
           "backlight": {"enabled": True, "brightness_percent": 70},
           "rotation": {"enabled": True, "direction": "keyboard"},
           "keyboard": {"hide_when_open": True},
            "wake": {"slider_open": True, "keyboard": True},
            "fingerprint": {"wake_screen": False}}

def snapshot():
    config = copy.deepcopy(_config)
    return {"config": config, "backend": "running", "error": None, "dimming_known": True,
            "status": {"schema_version": 2, "protocol_version": 2,
                "backend_version": "0.4.1", "instance_id": "visual-fixture",
                "heartbeat_monotonic": time.monotonic(),
                "observed_revision": config["revision"],
                "applied_revision": config["revision"], "slider": "open",
                "state": "active", "config": copy.deepcopy(config),
                "config_error": None,
                "capabilities": dict({name: True for name in ("backlight", "rotation", "keyboard", "slider_wake", "keyboard_wake", "fingerprint")}, backlight_dimming=False),
                "features": {name: {"state": "active", "message": ""}
                             for name in ("backlight", "rotation", "keyboard", "slider_wake", "keyboard_wake", "fingerprint")},
                "platform": {"overlay": "mounted", "reload_required": None}}}

def resume():
    return snapshot()

def save_patch(patch):
    for key, value in patch.items():
        if isinstance(value, dict):
            _config[key].update(value)
        else:
            _config[key] = value
    return snapshot()

def reset_defaults():
    return snapshot()

def probe():
    return {"ok": False, "error": "Visual fixture cannot run the confinement probe"}
'''

HARNESS = r'''// Staging-only real-compositor screenshot harness.
import QtQuick 2.12
import QtQuick.Window 2.2
import Lomiri.Components 1.3
import "qml" as App

Window {
    id: window
    visible: true
    width: units.gu(45)
    height: units.gu(80)
    color: "white"
    title: "Pro1 X Tweaks visual QA"
    Item {
        id: viewport
        anchors.centerIn: parent
        width: units.gu(40)
        height: units.gu(80)
        Rectangle { anchors.fill: parent; color: "white" }
        App.Main { id: app; anchors.fill: parent }
    }
    function positionScroll(item, atBottom) {
        if (typeof item.contentY !== "undefined") item.contentY = atBottom ? Math.max(0, item.contentHeight - item.height) : 0
        for (var index = 0; index < item.children.length; index++) positionScroll(item.children[index], atBottom)
    }
    function capture(name, next, atBottom) {
        var filename = Qt.resolvedUrl(name).toString().replace(/^file:\/\//, "")
        positionScroll(app, atBottom || false)
        viewport.grabToImage(function(result) {
            var ok = result.saveToFile(filename)
            console.log("SLIDER_UI_QA " + JSON.stringify({file: filename, ok: ok, width: app.width, height: app.height, dimming_available: app.dimmingAvailable}))
            if (!ok) Qt.quit()
            else next()
        })
    }
    Timer {
        interval: 3000; running: true; repeat: true
        onTriggered: {
            if (!app.ready || !app.config) return
            stop()
            window.capture("portrait.png", function() {
                window.capture("portrait-bottom.png", function() {
                    viewport.width = units.gu(80)
                    viewport.height = units.gu(40)
                    landscape.start()
                }, true)
            })
        }
    }
    Timer {
        id: landscape
        interval: 1000
        onTriggered: window.capture("landscape.png", function() {
            window.capture("landscape-controls.png", function() { Qt.quit() }, true)
        })
    }
}
'''

NATIVE_RUNNER = '''#!/usr/bin/python3
"""Launch only the isolated fixture on the current user's native compositor."""
import json
import os
from pathlib import Path
import shutil
import subprocess

work = Path(__file__).resolve().parent
environment = dict(os.environ)
source_pid = None
for process in Path("/proc").iterdir():
    if not process.name.isdigit():
        continue
    try:
        if process.stat().st_uid != os.getuid():
            continue
        command = (process / "cmdline").read_bytes().split(b"\\0")
        if not any(Path(item.decode(errors="replace")).name == "maliit-server" for item in command if item):
            continue
        values = dict(item.split(b"=", 1) for item in (process / "environ").read_bytes().split(b"\\0") if b"=" in item)
        for key, value in values.items():
            name = key.decode()
            if name.startswith(("MIR_", "QT_", "QML", "XDG_", "WAYLAND_", "DBUS_", "UBUNTU_", "LOMIRI_")) or name in ("LD_LIBRARY_PATH", "GRID_UNIT_PX"):
                environment[name] = value.decode()
        source_pid = process.name
        break
    except (OSError, ValueError):
        continue
if source_pid is None:
    raise SystemExit("Could not read this user's running maliit-server display environment")
if environment.get("QT_QPA_PLATFORM") in ("offscreen", "minimal", "minimalegl"):
    raise SystemExit("Refusing an offscreen/minimal backend: native rendering is required")
environment.update({"XDG_CONFIG_HOME": str(work / "sandbox/config"),
                    "XDG_CACHE_HOME": str(work / "sandbox/cache"),
                    "XDG_DATA_HOME": str(work / "sandbox/data"),
                    "GSETTINGS_BACKEND": "memory", "QML_DISABLE_DISK_CACHE": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"})
for directory in ("sandbox/config", "sandbox/cache", "sandbox/data"):
    (work / directory).mkdir(parents=True, exist_ok=True)
binary = shutil.which("qmlscene")
if binary is None:
    raise SystemExit("qmlscene is unavailable")
try:
    run = subprocess.run([binary, str(work / "Harness.qml")], cwd=work, env=environment,
                         text=True, capture_output=True, timeout=30)
    log = run.stdout + run.stderr
    code = run.returncode
except subprocess.TimeoutExpired as error:
    log = (error.stdout or b"").decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
    log += (error.stderr or b"").decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
    code = 124
(work / "native-render.log").write_text(log)
report = {"returncode": code, "display_source_pid": source_pid,
          "qpa_platform": environment.get("QT_QPA_PLATFORM"),
          "fixture_bridge": True,
          "captures": {name: (work / name).exists() for name in ("portrait.png", "portrait-bottom.png", "landscape.png", "landscape-controls.png")}}
(work / "capture-result.json").write_text(json.dumps(report, indent=2) + "\\n")
print(json.dumps(report, indent=2))
print(log)
raise SystemExit(0 if code == 0 and all(report["captures"].values()) else 1)
'''

BRIDGE_HARNESS = r'''// Native production QML -> PyOtherSide -> real bridge regression.
import QtQuick 2.12
import QtQuick.Window 2.2
import Lomiri.Components 1.3
import io.thp.pyotherside 1.5
import "qml" as App
Window {
    id: window
    width: units.gu(80); height: units.gu(45); visible: true
    property bool testReady: false
    property var transported: null
    property bool failureLatched: false
    App.Main { id: app; anchors.fill: parent }
    Python {
        id: py
        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl("python"))
            importModule("transport_qa", function(ok) { window.testReady = ok })
        }
        onError: { console.error(traceback); Qt.quit() }
    }
    function findSlider(item) {
        if (item.objectName === "brightnessSlider") return item
        for (var index = 0; index < item.children.length; index++) {
            var found = findSlider(item.children[index])
            if (found) return found
        }
        return null
    }
    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: {
            if (!app.ready || !app.config || !window.testReady || !app.dimmingAvailable) return
            stop()
            py.call("transport_qa.inspect_value", [Math.round(37.7)], function(value) {
                window.transported = value
                app.save({backlight: {brightness_percent: 38.5}})
                invalidVerify.start()
            })
        }
    }
    Timer {
        id: invalidVerify; interval: 3200
        onTriggered: py.call("gui_bridge.snapshot", [], function(result) {
            window.failureLatched = !!app.model.error && result.config.backlight.brightness_percent === 100
            console.log("SLIDER_INVALID_BRIGHTNESS_QA " + JSON.stringify({latched: window.failureLatched, persisted: result.config.backlight.brightness_percent, error: app.model.error}))
            var slider = window.findSlider(app)
            if (!slider) { console.error("Brightness slider not found"); Qt.quit(); return }
            slider.value = Math.round(37.7)
            verify.start()
        })
    }
    Timer {
        id: verify; interval: 1200
        onTriggered: py.call("gui_bridge.snapshot", [], function(result) {
            var slider = window.findSlider(app)
            console.log("SLIDER_BRIGHTNESS_QA " + JSON.stringify({ok: window.failureLatched && result.config.backlight.brightness_percent === 38 && slider.value === 38 && !app.model.error, transport: window.transported,
                persisted: result.config.backlight.brightness_percent, displayed: slider.value,
                save_error: app.model.error || null, backend: result.backend}))
            Qt.quit()
        })
    }
}
'''


def stage(destination, bridge_check=False):
    destination = stage_app(SOURCE, destination)
    if not bridge_check:
        (destination / "python/gui_bridge.py").write_text(FIXTURE_BRIDGE)
    main = destination / "qml/Main.qml"
    main.write_text(main.read_text().replace('applicationName: "pro1x-slider.lectops"',
                                            'applicationName: "pro1x-slider-uiqa.lectops"'))
    (destination / "Harness.qml").write_text(BRIDGE_HARNESS if bridge_check else HARNESS)
    runner = NATIVE_RUNNER
    if bridge_check:
        (destination / "python/transport_qa.py").write_text('def inspect_value(value):\n    return {"type": type(value).__name__, "value": value}\n')
        seed = '''import sys
import threading
import time
sys.path.insert(0, str(work / "python"))
from slider_common import APP_ID, default_config, save_config, load_config, atomic_json_write
fixture_preferences = Path(environment["XDG_CONFIG_HOME"]) / APP_ID / "config.json"
fixture_status = Path(environment["XDG_CACHE_HOME"]) / APP_ID / "status.json"
save_config(fixture_preferences, default_config())
def fixture_heartbeat():
    while True:
        config = load_config(fixture_preferences)
        atomic_json_write(fixture_status, {"schema_version":2, "protocol_version":2,
            "backend_version":"0.4.1", "instance_id":"isolated-dimming-qa",
            "heartbeat_monotonic":time.monotonic(), "observed_revision":config["revision"],
            "applied_revision":config["revision"], "slider":"open", "state":"active",
            "config":config, "config_error":None,
            "capabilities":dict({name:True for name in ("backlight", "rotation", "keyboard", "slider_wake", "keyboard_wake", "fingerprint")}, backlight_dimming=True),
            "features":{name:{"state":"active", "message":""} for name in ("backlight", "rotation", "keyboard", "slider_wake", "keyboard_wake", "fingerprint")},
            "platform":{"overlay":"mounted", "reload_required":None}})
        time.sleep(0.25)
threading.Thread(target=fixture_heartbeat, daemon=True).start()
'''
        runner = runner.replace('binary = shutil.which("qmlscene")', seed + 'binary = shutil.which("qmlscene")')
        runner = runner.replace('"fixture_bridge": True', '"fixture_bridge": False')
        runner = runner.replace('0 if code == 0 and all(report["captures"].values()) else 1',
                                '''0 if code == 0 and '"ok":true' in log else 1''')
    (destination / "capture-native.py").write_text(runner)
    compile(FIXTURE_BRIDGE, "fixture gui_bridge.py", "exec")
    compile(runner, "capture-native.py", "exec")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--host", help="SSH alias; use only with a connected, awake phone")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/pro1x_ed25519")
    parser.add_argument("--known-hosts", type=Path, default=Path.home() / ".ssh/pro1x_known_hosts")
    parser.add_argument("--host-key-alias", default="pro1x")
    parser.add_argument("--bridge-check", action="store_true", help="Exercise the production brightness save in isolated storage")
    args = parser.parse_args()
    destination = stage(args.destination, args.bridge_check)
    print("Staged native fixture:", destination, flush=True)
    if not args.host:
        print("Copy this directory to the phone, then run: python3 capture-native.py")
        return
    remote = "/home/phablet/slider-ui-qa-" + uuid.uuid4().hex[:10]
    connection = ["-i", str(args.identity), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                  "-o", "ConnectTimeout=10", "-o", "HostKeyAlias=" + args.host_key_alias,
                  "-o", "UserKnownHostsFile=" + str(args.known_hosts)]
    subprocess.run(["scp", *connection, "-r", str(destination), args.host + ":" + remote], check=True, timeout=35)
    command = "python3 " + shlex.quote(remote + "/capture-native.py")
    run = subprocess.run(["ssh", *connection, args.host, command], text=True, capture_output=True, timeout=40)
    print(run.stdout + run.stderr, flush=True)
    captures = destination / "captures"
    captures.mkdir(exist_ok=True)
    for name in ("portrait.png", "portrait-bottom.png", "landscape.png", "landscape-controls.png", "native-render.log", "capture-result.json"):
        if args.bridge_check and name.endswith(".png"):
            continue
        subprocess.run(["scp", *connection, args.host + ":" + remote + "/" + name, str(captures / name)], timeout=35)
    print("Remote isolated stage:", remote, flush=True)
    print("Local results:", captures, flush=True)
    raise SystemExit(run.returncode)


if __name__ == "__main__":
    main()
