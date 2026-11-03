# SPDX-License-Identifier: GPL-3.0-only
"""Confined, file-only asynchronous QML bridge. No device control lives here."""
import json
import math
import os
from pathlib import Path
import time
import uuid

from slider_common import (APP_ID, VERSION, PROTOCOL_VERSION, SCHEMA_VERSION,
                           atomic_json_write, config_path, default_config,
                           load_config, save_config, status_path, validate_config)

MAX_STATUS_BYTES = 16384
STALE_AFTER_SECONDS = 12
FEATURES = ("backlight", "rotation", "keyboard")
FEATURE_STATES = ("active", "idle", "disabled", "waiting", "error", "unsupported")


def _bounded_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_STATUS_BYTES + 1)
    if len(data) > MAX_STATUS_BYTES:
        raise ValueError("Backend status is too large")

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate backend status field")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=unique_fields)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("Invalid backend status JSON") from error


def _validate_status(value):
    """Reject malformed or incompatible reports before QML trusts any fields."""
    if not isinstance(value, dict):
        raise ValueError("Invalid backend status")
    required = {"schema_version", "protocol_version", "backend_version", "instance_id",
                "heartbeat_monotonic", "observed_revision", "applied_revision", "slider",
                "state", "config", "config_error", "capabilities", "features", "platform"}
    if required - set(value):
        raise ValueError("Backend status is incomplete")
    if (type(value.get("schema_version")) is not int
            or value["schema_version"] != SCHEMA_VERSION
            or type(value.get("protocol_version")) is not int
            or value["protocol_version"] != PROTOCOL_VERSION):
        raise ValueError("Backend protocol is incompatible; install matching components")
    for name in ("backend_version", "instance_id", "observed_revision"):
        if not isinstance(value.get(name), str) or not 1 <= len(value[name]) <= 128:
            raise ValueError("Invalid backend " + name)
    config = validate_config(value.get("config"))
    if value["observed_revision"] != config["revision"]:
        raise ValueError("Backend accepted configuration does not match its revision")
    applied = value.get("applied_revision")
    if applied is not None and applied != value["observed_revision"]:
        raise ValueError("Invalid backend applied revision")
    heartbeat = value.get("heartbeat_monotonic")
    if type(heartbeat) not in (int, float) or not math.isfinite(heartbeat) or heartbeat < 0:
        raise ValueError("Invalid backend heartbeat")
    if value.get("slider") not in ("open", "closed", "unknown"):
        raise ValueError("Invalid backend slider state")
    if value.get("state") not in ("starting", "active", "paused", "degraded", "recovery_needed"):
        raise ValueError("Invalid backend state")
    if value.get("config_error") is not None and not isinstance(value["config_error"], str):
        raise ValueError("Invalid backend configuration error")
    capabilities, features = value.get("capabilities"), value.get("features")
    if not isinstance(capabilities, dict) or not isinstance(features, dict):
        raise ValueError("Invalid backend feature report")
    for name in FEATURES:
        feature = features.get(name)
        if type(capabilities.get(name)) is not bool or not isinstance(feature, dict):
            raise ValueError("Invalid backend " + name + " capability")
        if feature.get("state") not in FEATURE_STATES or not isinstance(feature.get("message"), str):
            raise ValueError("Invalid backend " + name + " state")
    dimming = capabilities.get("backlight_dimming", False)
    if type(dimming) is not bool:
        raise ValueError("Invalid backend backlight dimming capability")
    platform = value.get("platform")
    if (not isinstance(platform, dict) or "reload_required" not in platform
            or platform.get("overlay") not in ("mounted", "unsupported", "missing", "unknown")):
        raise ValueError("Invalid backend platform report")
    if platform.get("reload_required") is not None and type(platform["reload_required"]) is not bool:
        raise ValueError("Invalid backend reload requirement")
    value = dict(value)
    value["capabilities"] = dict(capabilities, backlight_dimming=dimming)
    return value


class Bridge:
    """Testable file boundary; QML uses the module functions below."""

    def __init__(self, home=None, clock=time.monotonic):
        self.preferences = config_path(home)
        self.status_file = status_path(home)
        self.clock = clock
        self._last_config = default_config()
        self._has_baseline = False
        self._missing_config = True
        self._first_heartbeat = None
        self._verified_instance = None

    def _config(self):
        self._missing_config = False
        try:
            self._last_config = load_config(self.preferences)
            self._has_baseline = True
        except FileNotFoundError:
            self._missing_config = True
        return validate_config(self._last_config)

    def resume(self):
        # A cached heartbeat cannot establish liveness after a suspended launch.
        self._first_heartbeat = None
        self._verified_instance = None
        return self.snapshot()

    def snapshot(self):
        error = None
        try:
            config = self._config()
        except (OSError, ValueError) as failure:
            config = validate_config(self._last_config)
            error = "Could not read saved settings: " + str(failure)
        status, backend, dimming_known = None, "missing", False
        try:
            report = _bounded_json(self.status_file)
            status = _validate_status(report)
            dimming_known = "backlight_dimming" in report["capabilities"]
            heartbeat = status["heartbeat_monotonic"]
            marker = (status["instance_id"], heartbeat)
            age = self.clock() - heartbeat
            if age < -1 or age > STALE_AFTER_SECONDS:
                backend = "stale"
                self._verified_instance = None
                self._first_heartbeat = marker
            elif self._first_heartbeat is None:
                self._first_heartbeat = marker
                backend = "checking"
            elif self._verified_instance == marker[0]:
                backend = "running"
            elif marker[0] != self._first_heartbeat[0] or heartbeat > self._first_heartbeat[1]:
                self._verified_instance = marker[0]
                backend = "running"
            else:
                backend = "checking"
        except FileNotFoundError:
            self._first_heartbeat = None
            self._verified_instance = None
        except (OSError, ValueError) as failure:
            backend, status = "incompatible", None
            self._first_heartbeat = None
            self._verified_instance = None
            error = error or ("Could not read backend status: " + str(failure))
        if self._missing_config and backend == "running":
            # Recovery preferences belong to the watcher. A fresh compatible
            # report is the only authority when no preference file remains.
            self._last_config = validate_config(status["config"])
            self._has_baseline = True
            config = validate_config(self._last_config)
        return {"config": config, "status": status, "backend": backend, "error": error,
                "dimming_known": dimming_known}

    def _failure(self, failure):
        result = self.snapshot()
        result["error"] = "Settings were not saved: " + str(failure)
        return result

    def save_patch(self, patch):
        """Merge the small allowlisted preference surface; never accept paths."""
        try:
            before = self.snapshot()
            if before["backend"] == "incompatible":
                raise ValueError("The watcher is incompatible; install matching components before changing settings")
            if not isinstance(patch, dict) or not patch:
                raise ValueError("Expected a settings change")
            allowed = {"enabled": None,
                       "backlight": ("enabled", "brightness_percent"),
                       "rotation": ("enabled", "direction"),
                       "keyboard": ("hide_when_open",)}
            config = self._config()
            if self._missing_config and not self._has_baseline:
                raise ValueError("Waiting for a fresh watcher configuration; restore defaults explicitly to start a new configuration")
            for key, value in patch.items():
                if key not in allowed:
                    raise ValueError("Unknown setting: " + str(key))
                if allowed[key] is None:
                    config[key] = value
                else:
                    if not isinstance(value, dict) or not value or set(value) - set(allowed[key]):
                        raise ValueError("Unknown or empty " + key + " setting")
                    value = dict(value)
                    if key == "backlight" and "brightness_percent" in value:
                        brightness = value["brightness_percent"]
                        # JavaScript numbers cross PyOtherSide as doubles, even
                        # after Math.round(). Keep disk/backend schema strict.
                        if type(brightness) is float and math.isfinite(brightness) and brightness.is_integer():
                            value["brightness_percent"] = int(brightness)
                    config[key].update(value)
            config["revision"] = str(uuid.uuid4())
            self._last_config = save_config(self.preferences, validate_config(config))
        except (OSError, ValueError) as failure:
            return self._failure(failure)
        return self.snapshot()

    def reset_defaults(self):
        try:
            if self.snapshot()["backend"] == "incompatible":
                raise ValueError("The watcher is incompatible; install matching components before restoring defaults")
            self._last_config = save_config(self.preferences, default_config())
        except (OSError, ValueError) as failure:
            return self._failure(failure)
        return self.snapshot()


_bridge = None


def _instance():
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
    return _bridge


def snapshot():
    return _instance().snapshot()


def resume():
    return _instance().resume()


def save_patch(patch):
    return _instance().save_patch(patch)


def reset_defaults():
    return _instance().reset_defaults()


def probe():
    """Prove own-file access plus denial outside it; never change preferences."""
    token = str(uuid.uuid4())
    result_path = status_path().parent / "probe-result.json"
    result = {"ok": False, "app_id": APP_ID, "version": VERSION,
              "uid": os.getuid(), "outside_write_denied": False,
              "apparmor_label": None, "result_path": str(result_path)}
    paths = [config_path().parent / "confinement-probe.json",
             status_path().parent / "confinement-probe.json"]
    outside = Path.home() / ".cache" / "pro1x-slider-probe-outside.json"
    outside_created = False
    try:
        result["apparmor_label"] = Path("/proc/self/attr/current").read_text().strip()
    except OSError as error:
        result["label_error"] = str(error)
    try:
        for path in paths:
            atomic_json_write(path, {"token": token}, durable=True)
            if json.loads(path.read_text()) != {"token": token}:
                raise ValueError("Probe read did not match probe write")
        result["paths"] = [str(path) for path in paths]
        # Exclusive creation avoids changing a pre-existing helper scratch file.
        try:
            with outside.open("x") as stream:
                outside_created = True
                stream.write(token + "\n")
        except PermissionError:
            result["outside_write_denied"] = True
            result["ok"] = True
        else:
            result["error"] = "Confinement failed: a write outside the app directory was allowed"
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    finally:
        if outside_created:
            try:
                outside.unlink()
            except OSError as error:
                result["error"] = "Could not remove outside probe scratch: " + str(error)
                result["ok"] = False
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass
    try:
        atomic_json_write(result_path, result, durable=True)
    except (OSError, ValueError) as error:
        result["ok"] = False
        result["error"] = "Could not persist probe report: " + str(error)
    return result
