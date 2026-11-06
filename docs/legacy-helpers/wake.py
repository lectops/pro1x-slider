#!/usr/bin/python3
"""Wake Pro1 X on slider opening or a key press with the slider open."""
import fcntl
import os
from pathlib import Path
import select
import struct
import time

from gi.repository import Gio, GLib

EVENT = struct.Struct('@llHHi')
SLIDE = 10


def device(name):
    for path in Path('/sys/class/input').glob('event*'):
        if (path / 'device/name').read_text().strip() == name:
            return os.open('/dev/input/' + path.name, os.O_RDONLY | os.O_NONBLOCK)
    raise RuntimeError('Input device missing: ' + name)


def is_open(fd):
    switches = bytearray(8)
    fcntl.ioctl(fd, 0x8008451b, switches, True)
    return not bool(switches[SLIDE // 8] & (1 << (SLIDE % 8)))


def wake_event(source, event_type, code, value, opened):
    return ((source == 'slider' and event_type == 5 and code == SLIDE and value == 0)
            or (source == 'keyboard' and event_type == 1 and value == 1 and opened))


def main():
    slider = device('gpio-keys')
    keyboard = device('Fxtec Pro1')
    output = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(output, 0x40045564, 1)  # UI_SET_EVBIT(EV_KEY)
    for key in (143, 30, 44, 57, 28):  # KEY_WAKEUP and keyboard classification
        fcntl.ioctl(output, 0x40045565, key)
    os.write(output, struct.pack('80sHHHHI', b'Pro1X Keyboard Wake', 3, 1, 1, 1, 0)
             + bytes(1024))
    fcntl.ioctl(output, 0x5501)  # UI_DEV_CREATE
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    time.sleep(1)  # Allow the compositor to register the virtual keyboard.
    print('Ready: slider-open and keyboard-open wake enabled', flush=True)
    last_check = 0.0
    buffers = {slider: b'', keyboard: b''}
    try:
        while True:
            ready, _, _ = select.select([slider, keyboard], [], [])
            for fd in ready:
                data = os.read(fd, EVENT.size * 64)
                if not data:
                    raise RuntimeError('Input device disconnected')
                buffers[fd] += data
                while len(buffers[fd]) >= EVENT.size:
                    _, _, kind, code, value = EVENT.unpack(buffers[fd][:EVENT.size])
                    buffers[fd] = buffers[fd][EVENT.size:]
                    source = 'slider' if fd == slider else 'keyboard'
                    if not wake_event(source, kind, code, value, is_open(slider)):
                        continue
                    now = time.monotonic()
                    if now - last_check < 0.5:
                        continue
                    last_check = now
                    state = bus.call_sync(
                        'com.lomiri.SystemCompositor.Display',
                        '/com/lomiri/SystemCompositor/Display',
                        'org.freedesktop.DBus.Properties', 'Get',
                        GLib.Variant('(ss)', ('com.lomiri.SystemCompositor.Display',
                                              'ActiveOutputs')),
                        None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
                    if state[0] > 0:
                        continue
                    for event in ((1, 143, 1), (0, 0, 0), (1, 143, 0), (0, 0, 0)):
                        os.write(output, EVENT.pack(0, 0, *event))
                    print('Wake requested by ' + source, flush=True)
    finally:
        fcntl.ioctl(output, 0x5502)
        for fd in (slider, keyboard, output):
            os.close(fd)


if __name__ == '__main__':
    main()
