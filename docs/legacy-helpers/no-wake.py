#!/usr/bin/env python3
"""Suppress the Pro1 X reader's auxiliary keys, leaving its biometric HAL alone."""

import argparse
import fcntl
import os
from pathlib import Path
import signal
import struct
import time


EXPECTED_NAME = b"madev"
EXPECTED_ID = (0x1C, 0, 0, 0)
EXPECTED_KEYS = {116, *range(466, 473)}
EVIOCGRAB = 0x40044590
EVENT = struct.Struct("@llHHi")


def ioctl_read(fd, number, size):
    buf = bytearray(size)
    fcntl.ioctl(fd, 0x80000000 | (size << 16) | (ord("E") << 8) | number, buf)
    return bytes(buf)


def open_reader():
    matches = []
    for event in Path("/sys/class/input").glob("event*"):
        try:
            if (event / "device/name").read_text().strip() == EXPECTED_NAME.decode():
                matches.append(Path("/dev/input") / event.name)
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"Expected one madev input device, found {len(matches)}")
    path = matches[0]
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        name = ioctl_read(fd, 0x06, 256).split(b"\0", 1)[0]
        identity = struct.unpack("HHHH", ioctl_read(fd, 0x02, 8))
        bits = int.from_bytes(ioctl_read(fd, 0x21, 96), "little")
        keys = {i for i in range(768) if bits & (1 << i)}
        if name != EXPECTED_NAME or identity != EXPECTED_ID or keys != EXPECTED_KEYS:
            raise RuntimeError("Fingerprint input identity/capabilities changed; refusing to grab")
    except BaseException:
        os.close(fd)
        raise
    return fd, path


def stop(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate the input device without grabbing it")
    args = parser.parse_args()
    fd, path = open_reader()
    grabbed = False
    try:
        print(f"Validated Pro1 X fingerprint auxiliary input: {path}", flush=True)
        if args.check:
            return
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        fcntl.ioctl(fd, EVIOCGRAB, 1)
        grabbed = True
        print("Reader auxiliary keys suppressed; biometric device untouched", flush=True)
        last_report = float("-inf")
        while True:
            data = os.read(fd, EVENT.size * 64)
            if not data or len(data) % EVENT.size:
                raise RuntimeError("Fingerprint input stream ended or returned a partial event")
            for _, _, kind, code, value in EVENT.iter_unpack(data):
                if kind == 1 and code == 116 and value == 1:
                    now = time.monotonic()
                    if now - last_report >= 5:
                        print("Suppressed reader power-button event", flush=True)
                        last_report = now
    finally:
        try:
            if grabbed:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
        finally:
            os.close(fd)


if __name__ == "__main__":
    main()
