#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Follow the Pro1 X keypad slide switch without grabbing keyboard input."""
import fcntl
import glob
import os
from pathlib import Path
import struct

LIGHT = Path('/sys/class/leds/:kbd_backlight/brightness')
EVENT = struct.Struct('@llHHi')
SW_KEYPAD_SLIDE = 10


def light(opened):
    LIGHT.write_text('255' if opened else '0')


def main():
    for node in glob.glob('/sys/class/input/event*'):
        device = Path(node) / 'device'
        if (device / 'name').read_text().strip() != 'gpio-keys':
            continue
        if not int((device / 'capabilities/sw').read_text().replace(' ', ''), 16) & (1 << SW_KEYPAD_SLIDE):
            continue
        with open('/dev/input/' + Path(node).name, 'rb', buffering=0) as stream:
            def sync():
                state = bytearray(8)
                # EVIOCGSW(8): query current switches, including startup state.
                fcntl.ioctl(stream, 0x8008451b, state, True)
                light(not bool(state[SW_KEYPAD_SLIDE // 8] & (1 << (SW_KEYPAD_SLIDE % 8))))

            sync()
            while True:
                raw = stream.read(EVENT.size)
                if len(raw) != EVENT.size:
                    raise RuntimeError('Input device disconnected')
                _, _, kind, code, value = EVENT.unpack(raw)
                if kind == 5 and code == SW_KEYPAD_SLIDE:
                    light(not bool(value))
                elif kind == 0 and code == 3:
                    sync()
        return
    raise RuntimeError('No gpio-keys keypad slide switch found')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
