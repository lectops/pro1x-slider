#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""One slider reader for independent backlight, rotation, and OSK handlers."""
import argparse
import fcntl
import glob
import json
import logging
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time

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
    def __init__(self, settings, journal, target, key, values, targets, guard=None):
        self.settings, self.journal, self.target = settings, Path(journal), target
        self.key, self.values, self.targets = key, values, targets
        self.guard = guard
        self.saved = None
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        if self.journal.exists():
            saved = json.loads(self.journal.read_text())[self.key]
            if saved not in self.values:
                raise RuntimeError('Invalid saved ' + self.key)
            self.saved = saved
        self.loaded = True

    def apply(self, opened):
        self.load()
        if opened:
            if self.guard:
                self.guard()
            target = self.target() if callable(self.target) else self.target
            if target not in self.targets:
                raise RuntimeError('Invalid ' + self.key + ' target: ' + target)
            if self.saved is None:
                saved = self.settings.get()
                self.journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = self.journal.with_suffix('.tmp')
                with temporary.open('w') as stream:
                    os.chmod(temporary, 0o600)
                    json.dump({self.key: saved}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.journal)
                directory = os.open(self.journal.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                self.saved = saved
            self.settings.set(target)
        else:
            self.restore()

    def restore(self):
        self.load()
        if self.saved is not None:
            self.settings.set(self.saved)
            self.journal.unlink()
            self.saved = None


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
        self.done = {}
        self.errors = {}

    def apply(self, opened):
        handlers = [('light', lambda: self.brightness.write_text('255' if opened else '0')),
                    ('rotation', lambda: self.rotation.apply(opened))]
        if self.keyboard is not None:
            handlers.append(('osk', lambda: self.keyboard.apply(opened)))
        for name, callback in handlers:
            if self.done.get(name) == opened:
                continue
            try:
                callback()
                self.done[name] = opened
                self.errors.pop(name, None)
                LOG.info('%s: keyboard %s', name, 'open' if opened else 'closed')
            except Exception as error:
                self.done.pop(name, None)
                message = str(error)
                if self.errors.get(name) != message:
                    LOG.error('%s: %s (will retry)', name, message)
                    self.errors[name] = message


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
    parser.add_argument('--orientation', choices=ORIENTATIONS)
    parser.add_argument('--restore-only', action='store_true')
    parser.add_argument('--state-dir', type=Path,
                        default=Path(os.environ.get('SLIDER_STATE_DIR',
                                     str(Path.home() / '.local/state/pro1x-slider'))))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / 'watcher.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def orientation():
            config = args.state_dir / 'landscape-orientation'
            return args.orientation or (config.read_text().strip() if config.exists() else ORIENTATIONS[0])

        rotation = Rotation(Settings(), args.state_dir / 'saved-orientation.json', orientation,
                            guard=require_overlay)
        keyboard = Keyboard(Settings('com.lomiri.keyboard.maliit', 'stay-hidden', ('false', 'true')),
                            args.state_dir / 'saved-keyboard.json')
        if args.restore_only:
            restore_settings(rotation, keyboard)
            return
        controller = Controller(rotation, keyboard=keyboard)
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            with open(find_device(), 'rb', buffering=0) as stream:
                opened = is_open(stream)
                controller.apply(opened)
                dropped = False
                while not stopping:
                    ready, _, _ = select.select([stream], [], [], 2)
                    if ready:
                        raw = stream.read(EVENT.size)
                        if len(raw) != EVENT.size:
                            raise RuntimeError('Slide input device disconnected')
                        _, _, kind, code, value = EVENT.unpack(raw)
                        if kind == 0 and code == 3:  # SYN_DROPPED: ignore until SYN_REPORT
                            dropped = True
                        elif dropped:
                            if kind == 0 and code == 0:
                                opened = is_open(stream)
                                dropped = False
                        elif kind == 5 and code == SW_KEYPAD_SLIDE:
                            opened = not bool(value)
                    controller.apply(opened)
        finally:
            for attempt in range(3):
                try:
                    restore_settings(rotation, keyboard)
                    break
                except Exception:
                    LOG.exception('Could not restore settings; saved state retained for next start')
                    if attempt < 2:
                        time.sleep(1)
            # Preserve the backlight's established behavior when this service stops.


if __name__ == '__main__':
    main()
