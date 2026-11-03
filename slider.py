#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""One slider reader for independent backlight, rotation, and OSK handlers."""
import argparse
import fcntl
import glob
import hashlib
import logging
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time
import uuid

from slider_common import (VERSION, PROTOCOL_VERSION, default_config, validate_config,
                           config_path, status_path, load_config, save_config,
                           read_json, atomic_json_write)

EVENT = struct.Struct('@llHHi')
SW_KEYPAD_SLIDE = 10
LIGHT = Path('/sys/class/leds/:kbd_backlight/brightness')
SCHEMA = 'com.lomiri.touch.system'
ORIENTATIONS = ('InvertedLandscapeOrientation', 'LandscapeOrientation')
ALL_ORIENTATIONS = ('none', 'PrimaryOrientation', 'LandscapeOrientation',
                    'InvertedLandscapeOrientation', 'PortraitOrientation',
                    'InvertedPortraitOrientation')
LOG = logging.getLogger('pro1x-slider')


class Settings:
    def __init__(self, schema=SCHEMA, key='orientation-lock', values=ALL_ORIENTATIONS):
        self.schema, self.key, self.values = schema, key, values

    def call(self, action, value=None):
        runtime = Path('/run/user') / str(os.getuid())
        if not (runtime / 'bus').exists():
            raise RuntimeError('Waiting for the desktop session bus')
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(runtime / 'bus'))
        cmd = ['/usr/bin/gsettings', action, self.schema, self.key]
        if value is not None:
            cmd.append(value)
        result = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=5)
        # gsettings may return zero even when dconf could not commit the write.
        if result.returncode or result.stderr.strip():
            raise RuntimeError(result.stderr.strip() or 'gsettings failed')
        return result.stdout.strip().strip("'")

    def get(self):
        value = self.call('get')
        if value not in self.values:
            raise RuntimeError('Unexpected ' + self.key + ' setting: ' + value)
        return value

    def set(self, value):
        self.call('set', value)
        if self.get() != value:
            raise RuntimeError(self.key + ' setting did not persist')


class SavedSetting:
    """Write-ahead recovery with conservative ownership checks.

    A prepared record accepts either side of an interrupted write. An applied
    record accepts only its own value; a different value releases ownership for
    this open cycle. GSettings cannot distinguish same-value external writes.
    """
    def __init__(self, settings, journal, target, key, values, targets, guard=None):
        self.settings, self.journal, self.target = settings, Path(journal), target
        self.key, self.values, self.targets = key, values, targets
        self.guard = guard
        self.saved = None
        self.loaded = False
        self.record = None

    def load(self):
        if self.loaded:
            return
        if self.journal.exists():
            try:
                record = read_json(self.journal)
            except (ValueError, OSError) as error:
                raise RuntimeError('Invalid recovery journal: ' + str(error)) from error
            if not isinstance(record, dict) or record.get(self.key) not in self.values:
                raise RuntimeError('Invalid saved ' + self.key)
            if 'schema_version' not in record:
                # Legacy journals know the baseline but not which direction was
                # forced. Only known override values may be recovered.
                record = dict(record, schema_version=1, phase='legacy')
            if record.get('schema_version') != 1 or record.get('phase') not in ('legacy', 'prepared', 'applied', 'restoring', 'released'):
                raise RuntimeError('Invalid recovery journal')
            if record['phase'] not in ('legacy', 'released'):
                if record.get('last_applied') not in self.targets or record.get('previous') not in self.values:
                    raise RuntimeError('Invalid recovery values')
            self.record, self.saved = record, record[self.key]
        self.loaded = True

    def persist(self, phase, **fields):
        record = dict(self.record or {}, **fields)
        record.update({self.key: self.saved, 'schema_version': 1, 'phase': phase})
        atomic_json_write(self.journal, record, durable=True)
        self.record = record

    def owns(self, current):
        phase = self.record['phase']
        if phase == 'released':
            return False
        if phase == 'legacy':
            return current in self.targets or current == self.saved
        candidates = {self.record['last_applied']}
        if phase in ('prepared', 'restoring'):
            candidates.add(self.record['previous'])
        if phase == 'restoring':
            candidates.add(self.saved)
        return current in candidates

    def apply(self, opened):
        self.load()
        if not opened:
            return self.restore()
        if self.record and self.record['phase'] == 'released':
            return
        if self.guard:
            self.guard()
        target = self.target() if callable(self.target) else self.target
        if target not in self.targets:
            raise RuntimeError('Invalid ' + self.key + ' target')
        current = self.settings.get()
        if self.record and not self.owns(current):
            self.persist('released')
            return
        if self.saved is None:
            self.saved = current
        if self.record and self.record['phase'] == 'applied' and current == target:
            return
        self.persist('prepared', previous=current, last_applied=target)
        if current != target:
            self.settings.set(target)
        self.persist('applied')

    def restore(self):
        self.load()
        if self.saved is None:
            return
        if self.record['phase'] != 'released':
            current = self.settings.get()
            if self.owns(current):
                # Legacy recovery gains a complete journal before restoring.
                target = self.record.get('last_applied', current if current in self.targets else self.targets[0])
                self.persist('restoring', previous=current, last_applied=target)
                if current != self.saved:
                    self.settings.set(self.saved)
        self.journal.unlink()
        self.saved, self.record = None, None


class Rotation(SavedSetting):
    def __init__(self, settings, journal, orientation, guard=None):
        super().__init__(settings, journal, orientation, 'orientation-lock',
                         ALL_ORIENTATIONS, ORIENTATIONS, guard)


class Keyboard(SavedSetting):
    def __init__(self, settings, journal):
        super().__init__(settings, journal, 'true', 'stay-hidden', ('false', 'true'), ('true',))


def restore_settings(rotation, keyboard):
    errors = []
    for name, handler in [('rotation', rotation), ('osk', keyboard)]:
        try:
            handler.restore()
        except Exception as error:
            errors.append(name + ': ' + str(error))
    if errors:
        raise RuntimeError('; '.join(errors))


class Controller:
    def __init__(self, rotation, brightness=LIGHT, keyboard=None):
        self.rotation, self.brightness = rotation, Path(brightness)
        self.keyboard = keyboard
        self.done, self.errors, self.features = {}, {}, {}
        self.opened = None
        self.last_brightness = None

    def apply(self, opened, config=None):
        configured = config is not None
        config = validate_config(config) if configured else default_config()
        master = config['enabled']
        if configured:
            self.rotation.target = ORIENTATIONS[config['rotation']['direction'] == 'reversed']
        def light():
            requested = master and config['backlight']['enabled']
            if not requested and not self.brightness.exists():
                if self.last_brightness not in (None, 0):
                    raise RuntimeError('Managed backlight disappeared; cannot confirm it is off')
                return
            if opened is None and master and config['backlight']['enabled']:
                return
            maximum_file = self.brightness.with_name('max_brightness')
            maximum = int(maximum_file.read_text()) if maximum_file.exists() else 255
            if not 1 <= maximum <= 65535:
                raise RuntimeError('Invalid backlight maximum')
            # The supported Pro1-X uses gpio-leds: every nonzero value is on.
            # max_brightness is an API range, not evidence of physical dimming.
            # Retain brightness_percent in the file protocol for draft config
            # compatibility; the feature toggle exclusively controls this light.
            value = maximum if opened and requested else 0
            # Read actual hardware to recover after resume or other writers.
            if not self.brightness.exists() or self.brightness.read_text().strip() != str(value):
                self.brightness.write_text(str(value))
            self.last_brightness = value
        def preference(handler, enabled):
            handler.load()
            if opened is not False and handler.record and handler.record['phase'] == 'released':
                return
            if opened is None and enabled:
                return
            handler.apply(bool(opened and enabled))
            if enabled and opened is False:
                # Enabled requests require a supported backend even while idle.
                # Restore pending ownership first, then report unavailable support.
                if handler.guard:
                    handler.guard()
                handler.settings.get()
        handlers = [('backlight', light, master and config['backlight']['enabled']),
                    ('rotation', lambda: preference(self.rotation, master and config['rotation']['enabled']), master and config['rotation']['enabled'])]
        if self.keyboard is not None:
            handlers.append(('keyboard', lambda: preference(self.keyboard, master and config['keyboard']['hide_when_open']), master and config['keyboard']['hide_when_open']))
        # A physical close ends relinquishment even when a feature is disabled.
        for name, callback, enabled in handlers:
            try:
                callback()
                self.errors.pop(name, None)
                handler = self.rotation if name == 'rotation' else self.keyboard if name == 'keyboard' else None
                released = bool(handler and handler.record and handler.record['phase'] == 'released')
                self.features[name] = {'state': 'disabled' if not enabled else 'waiting' if opened is None else 'idle' if not opened or released else 'active',
                                       'message': 'External preference retained until next slider cycle' if released else ''}
            except Exception as error:
                message = str(error)
                if self.errors.get(name) != message:
                    LOG.error('%s: %s (will retry)', name, message)
                self.errors[name] = message
                state = 'waiting' if 'session' in message.lower() else 'unsupported' if 'override' in message.lower() or 'overlay' in message.lower() else 'error'
                self.features[name] = {'state': state, 'message': message}
        self.opened = opened
        return not self.errors


class ConfigStore:
    def __init__(self, state_dir, path=None):
        self.path = Path(path) if path is not None else config_path()
        self.checkpoint = Path(state_dir) / 'last-known-good.json'
        self.config, self.error = default_config(), None
        if self.checkpoint.exists():
            try:
                self.config = load_config(self.checkpoint)
            except (ValueError, OSError) as error:
                self.error = 'Invalid saved configuration: ' + str(error)
                # Do not silently adopt defaults over an unreadable checkpoint.
                self.config['enabled'] = False

    def refresh(self):
        try:
            incoming = load_config(self.path)
            if incoming != self.config or not self.checkpoint.exists():
                save_config(self.checkpoint, incoming)
                self.config = incoming
            self.error = None
        except FileNotFoundError:
            # Removal is not a reset command; persist the initial defaults once.
            if not self.checkpoint.exists():
                try:
                    save_config(self.checkpoint, self.config)
                except OSError as error:
                    self.error = str(error)
        except (ValueError, OSError) as error:
            self.error = str(error)
        return self.config


def overlay_status():
    try:
        require_overlay()
        # Mount identity cannot establish whether this running shell loaded it.
        return {'overlay': 'mounted', 'reload_required': None}
    except (OSError, RuntimeError):
        from qml_patch import TARGET, STOCK_SHA256
        try:
            supported = hashlib.sha256(Path(TARGET).read_bytes()).hexdigest() == STOCK_SHA256
            return {'overlay': 'missing' if supported else 'unsupported', 'reload_required': None}
        except OSError:
            return {'overlay': 'unknown', 'reload_required': None}


def make_status(store, controller, instance, opened, device_error=None):
    features = dict(controller.features)
    for name in ('backlight', 'rotation', 'keyboard'):
        features.setdefault(name, {'state': 'waiting', 'message': 'Waiting for slider input'})
    platform = overlay_status()
    errors = bool(controller.errors or store.error or device_error)
    recovery = any('journal' in item['message'].lower() or 'saved' in item['message'].lower() for item in features.values() if item['state'] == 'error')
    state = 'recovery_needed' if recovery else 'degraded' if errors else 'starting' if opened is None else 'paused' if not store.config['enabled'] else 'active'
    if device_error:
        features['backlight'] = {'state': 'waiting', 'message': device_error}
    return {'schema_version': 1, 'protocol_version': PROTOCOL_VERSION, 'backend_version': VERSION,
            'instance_id': instance, 'heartbeat_monotonic': time.monotonic(),
            'observed_revision': store.config['revision'],
            'applied_revision': store.config['revision'] if not errors and opened is not None else None,
            'slider': 'unknown' if opened is None else 'open' if opened else 'closed',
            'state': state, 'config': store.config, 'config_error': store.error,
            'capabilities': {'backlight': controller.brightness.exists(),
                             'backlight_dimming': False,
                             'rotation': platform['overlay'] == 'mounted',
                             'keyboard': features['keyboard']['state'] not in ('error', 'unsupported')},
            'features': features, 'platform': platform}


def find_device():
    for node in sorted(glob.glob('/sys/class/input/event*')):
        device = Path(node) / 'device'
        if (device / 'name').read_text().strip() == 'gpio-keys':
            switches = int((device / 'capabilities/sw').read_text().replace(' ', ''), 16)
            if switches & (1 << SW_KEYPAD_SLIDE):
                return '/dev/input/' + Path(node).name
    raise RuntimeError('No gpio-keys keypad slide switch found')


def is_open(stream):
    state = bytearray(8)
    fcntl.ioctl(stream, 0x8008451b, state, True)  # EVIOCGSW(8)
    return not bool(state[SW_KEYPAD_SLIDE // 8] & (1 << (SW_KEYPAD_SLIDE % 8)))


def require_overlay():
    target = Path('/usr/share/lomiri/OrientedShell.qml')
    overlay = Path('/userdata/system-data/pro1x-slider/OrientedShell.qml')
    if not overlay.exists() or not os.path.samefile(target, overlay):
        raise RuntimeError('Lomiri override is not mounted; backlight remains available')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore-only', action='store_true')
    parser.add_argument('--state-dir', type=Path,
                        default=Path(os.environ.get('SLIDER_STATE_DIR',
                                     str(Path.home() / '.local/state/pro1x-slider'))))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / 'watcher.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rotation = Rotation(Settings(), args.state_dir / 'saved-orientation.json', ORIENTATIONS[0],
                            guard=require_overlay)
        keyboard = Keyboard(Settings('com.lomiri.keyboard.maliit', 'stay-hidden', ('false', 'true')),
                            args.state_dir / 'saved-keyboard.json')
        if args.restore_only:
            restore_settings(rotation, keyboard)
            return
        controller = Controller(rotation, keyboard=keyboard)
        store = ConfigStore(args.state_dir)
        instance = str(uuid.uuid4())
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        stream, opened, device_error = None, None, None
        try:
            while not stopping:
                config = store.refresh()
                try:
                    if stream is None:
                        stream = open(find_device(), 'rb', buffering=0)
                    # Query current kernel state every iteration: buffered events,
                    # suspend and dropped events cannot leave a stale open flag.
                    opened = is_open(stream)
                    device_error = None
                    controller.apply(opened, config)
                except (OSError, RuntimeError) as error:
                    device_error, opened = str(error), None
                    if stream is not None:
                        stream.close()
                        stream = None
                    # Disabled features restore even without input; enabled ones
                    # wait without inventing a physical close/open cycle.
                    controller.apply(None, config)
                try:
                    atomic_json_write(status_path(), make_status(store, controller, instance, opened, device_error))
                except (ValueError, OSError):
                    LOG.exception('Could not publish status')
                if stream is None:
                    time.sleep(2)
                else:
                    try:
                        ready, _, _ = select.select([stream], [], [], 2)
                        if ready:
                            raw = stream.read(EVENT.size)
                            if len(raw) != EVENT.size:
                                stream.close()
                                stream = None
                    except OSError:
                        stream.close()
                        stream = None
        finally:
            if stream is not None:
                stream.close()
            for attempt in range(3):
                try:
                    restore_settings(rotation, keyboard)
                    break
                except Exception:
                    LOG.exception('Could not restore settings; saved state retained for next start')
                    if attempt < 2:
                        time.sleep(1)
            try:
                controller.brightness.write_text('0')
            except OSError:
                LOG.exception('Could not turn backlight off')


if __name__ == '__main__':
    main()
