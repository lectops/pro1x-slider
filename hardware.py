# SPDX-License-Identifier: GPL-3.0-only
"""Optional wake and fingerprint input handling owned by the single watcher."""
import fcntl
import logging
import os
from pathlib import Path
import struct
import time

from keyboard_gate import KeyboardGate

EVENT = struct.Struct('@llHHi')
KEY_WAKEUP = 143
EVIOCGRAB = 0x40044590
FINGERPRINT_KEYS = {116, *range(466, 473)}
KEYBOARD_WAKE = Path('/sys/bus/i2c/devices/0-005b/power/wakeup')
INPUT_CLASS = Path('/sys/class/input')
INPUT_DEVICES = Path('/dev/input')
LOG = logging.getLogger('pro1x-slider')


def ioctl_read(fd, number, size):
    buf = bytearray(size)
    fcntl.ioctl(fd, 0x80000000 | (size << 16) | (ord('E') << 8) | number, buf)
    return bytes(buf)


def open_input(name, fingerprint=False):
    matches = []
    for event in INPUT_CLASS.glob('event*'):
        try:
            if (event / 'device/name').read_text().strip() == name:
                matches.append(INPUT_DEVICES / event.name)
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise RuntimeError('Expected one ' + name + ' input device')
    fd = os.open(matches[0], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if ioctl_read(fd, 0x06, 256).split(b'\0', 1)[0].decode() != name:
            raise RuntimeError('Input device identity changed')
        if name == 'Fxtec Pro1' and struct.unpack('HHHH', ioctl_read(fd, 0x02, 8)) != (0x18, 0x9523, 0x701, 1):
            raise RuntimeError('Unsupported hardware keyboard identity')
        if fingerprint:
            identity = struct.unpack('HHHH', ioctl_read(fd, 0x02, 8))
            bits = int.from_bytes(ioctl_read(fd, 0x21, 96), 'little')
            keys = {i for i in range(768) if bits & (1 << i)}
            if identity != (0x1c, 0, 0, 0) or keys != FINGERPRINT_KEYS:
                raise RuntimeError('Unsupported fingerprint input identity or capabilities')
    except BaseException:
        os.close(fd)
        raise
    return fd


def keyboard_wake_supported():
    try:
        return KEYBOARD_WAKE.read_text().strip() == 'enabled'
    except OSError:
        return False


class WakeOutput:
    def __init__(self):
        # Lazy import keeps the portable suite and non-wake features usable
        # without PyGObject. Display state is checked before every wake request.
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.screen_off()
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.created = False
        try:
            fcntl.ioctl(self.fd, 0x40045564, 1)
            for key in (KEY_WAKEUP, 30, 44, 57, 28):
                fcntl.ioctl(self.fd, 0x40045565, key)
            os.write(self.fd, struct.pack('80sHHHHI', b'Pro1 X Tweaks Wake', 3, 1, 1, 1, 0) + bytes(1024))
            fcntl.ioctl(self.fd, 0x5501)
            self.created = True
        except BaseException:
            self.close()
            raise
        self.ready_at = time.monotonic() + 1
        self.last_request = float('-inf')

    def screen_off(self):
        result = self.bus.call_sync(
            'com.lomiri.SystemCompositor.Display', '/com/lomiri/SystemCompositor/Display',
            'org.freedesktop.DBus.Properties', 'Get',
            self.GLib.Variant('(ss)', ('com.lomiri.SystemCompositor.Display', 'ActiveOutputs')),
            None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        if len(result) != 2 or any(int(value) < 0 for value in result):
            raise RuntimeError('Unexpected compositor display state')
        return int(result[0]) == 0 and int(result[1]) == 0

    def wake(self):
        now = time.monotonic()
        if now < self.ready_at or now - self.last_request < 0.5:
            return False
        self.last_request = now
        if not self.screen_off():
            return False
        for event in ((1, KEY_WAKEUP, 1), (0, 0, 0), (1, KEY_WAKEUP, 0), (0, 0, 0)):
            os.write(self.fd, EVENT.pack(0, 0, *event))
        return True

    def close(self):
        if self.fd is not None:
            try:
                if self.created:
                    fcntl.ioctl(self.fd, 0x5502)
            finally:
                os.close(self.fd)
                self.fd = None


class Hardware:
    def __init__(self):
        self.keyboard = self.fingerprint = self.output = None
        self.gate = None
        self.features, self.capabilities = {}, {}
        self.slider_enabled = self.keyboard_enabled = False

    def report(self, name, state, message='', supported=True):
        self.features[name] = {'state': state, 'message': message}
        self.capabilities[name] = supported

    def release(self, name):
        if name == 'keyboard' and self.gate is not None:
            gate, self.gate = self.gate, None
            gate.close()
        fd = getattr(self, name)
        if fd is not None:
            # Closing also releases an exclusive fingerprint grab on failure.
            setattr(self, name, None)
            os.close(fd)

    def reconcile(self, config, opened):
        master = config['enabled']
        self.slider_enabled = master and config['wake']['slider_open']
        self.keyboard_enabled = master and config['wake']['keyboard']
        supported = keyboard_wake_supported()
        need_output = self.slider_enabled or (self.keyboard_enabled and supported)
        output_error = None
        try:
            if need_output and self.output is None:
                self.output = WakeOutput()
            elif not need_output and self.output is not None:
                self.output.close()
                self.output = None
        except Exception as error:
            output_error = str(error)
        if not self.slider_enabled:
            self.report('slider_wake', 'disabled')
        elif output_error:
            self.report('slider_wake', 'error', output_error, False)
        else:
            self.report('slider_wake', 'waiting' if opened is None else 'active',
                        'Waiting for slider input' if opened is None else '')
        try:
            need_keyboard = master and supported
            if self.gate is not None and (not need_keyboard or self.keyboard_enabled):
                self.release('keyboard')
            if need_keyboard:
                if self.keyboard is None:
                    self.keyboard = open_input('Fxtec Pro1')
                if not self.keyboard_enabled:
                    if self.gate is None:
                        self.gate = KeyboardGate(self.keyboard)
                    if self.gate.error:
                        raise RuntimeError(self.gate.error)
            else:
                self.release('keyboard')
            if not self.keyboard_enabled:
                self.report('keyboard_wake', 'disabled',
                            'Key wake blocked while the screen is off' if self.gate is not None else 'System keyboard wake behavior',
                            supported=supported)
            elif not supported:
                self.report('keyboard_wake', 'unsupported', 'Requires the keyboard-wake kernel; see installation notes', False)
            elif output_error:
                self.report('keyboard_wake', 'error', output_error, supported)
            else:
                self.report('keyboard_wake', 'waiting' if opened is None else 'active' if opened else 'idle')
        except Exception as error:
            self.report('keyboard_wake', 'error', str(error), supported)
        try:
            block = master and not config['fingerprint']['wake_screen']
            if block:
                if self.fingerprint is None:
                    fd = open_input('madev', fingerprint=True)
                    try:
                        fcntl.ioctl(fd, EVIOCGRAB, 1)
                    except BaseException:
                        os.close(fd)
                        raise
                    self.fingerprint = fd
                self.report('fingerprint', 'active', 'Screen wake blocked; fingerprint unlock remains available')
            else:
                self.release('fingerprint')
                self.report('fingerprint', 'disabled', 'System fingerprint wake behavior')
        except Exception as error:
            self.report('fingerprint', 'error', str(error), False)

    def descriptors(self):
        return [fd for fd in (self.keyboard, self.fingerprint) if fd is not None]

    def event(self, source, kind, code, value, opened):
        requested = ((source == 'slider' and self.slider_enabled and kind == 5 and code == 10 and value == 0)
                     or (source == 'keyboard' and self.keyboard_enabled and kind == 1 and value == 1 and opened is True))
        if requested and self.output is not None:
            name = 'slider_wake' if source == 'slider' else 'keyboard_wake'
            try:
                if self.output.wake():
                    LOG.info('Wake requested by %s', source)
            except Exception as error:
                self.report(name, 'error', str(error), False)
                self.output.close()
                self.output = None

    def consume(self, fd, opened):
        source = 'keyboard' if fd == self.keyboard else 'fingerprint'
        try:
            data = os.read(fd, EVENT.size * 64)
            if not data or len(data) % EVENT.size:
                raise RuntimeError('Input device disconnected or returned a partial event')
            # Fingerprint evdev carries only auxiliary keys. Never open the
            # biometric character device or relay keys from this descriptor.
            if source == 'keyboard':
                for _, _, kind, code, value in EVENT.iter_unpack(data):
                    self.event(source, kind, code, value, opened)
        except BlockingIOError:
            pass
        except (OSError, RuntimeError) as error:
            self.release(source)
            self.report('keyboard_wake' if source == 'keyboard' else 'fingerprint', 'error', str(error), False)

    def close(self):
        for name in ('fingerprint', 'keyboard'):
            try:
                self.release(name)
            except OSError:
                LOG.exception('Could not close %s input', name)
        if self.output is not None:
            try:
                self.output.close()
            except OSError:
                LOG.exception('Could not close wake output')
            finally:
                self.output = None
