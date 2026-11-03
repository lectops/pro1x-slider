# Validation and compatibility

## Recorded working configuration

| Component | Tested value |
| --- | --- |
| Phone | F(x)tec Pro1-X / QX1050, ARM64 |
| Ubuntu Touch | 24.04-2.2 stable |
| Base OS | Ubuntu 24.04.5 |
| Lomiri | `0.5.0+0~20260910085057.5+ubports24.04.2~1.gbp0bd07c` |
| QML file | `/usr/share/lomiri/OrientedShell.qml` |
| SHA-256 | `633578d225f0dd41a656b27c08e0d5cc0901e6b6267c74c26edab8823524dec8` |
| Slide input | `gpio-keys`, `SW_KEYPAD_SLIDE` code 10; 0 open, 1 closed |
| Backlight | `/sys/class/leds/:kbd_backlight/brightness`, 255 open / 0 closed |
| Correct open orientation | `InvertedLandscapeOrientation` |

The hardware checks were performed on 3 November 2026. The owner confirmed that the combined behavior worked and that the corrected landscape direction was right-side up over the keyboard. Closing the keyboard restored the exact-orientation setting to `none` on this installation. The normal `rotation-lock` preference remained untouched.

The combined service was enabled and active after a phone reboot. Its main process started about 11 seconds into that boot. The old backlight service was disabled, and logs showed both handlers responding to open and close events.

The public source is an export of that working implementation, with the corrected direction as the default, license headers, and standalone documentation. It has not been installed on a second phone.

## Automated checks

All 17 Python tests passed on the phone and on the development Mac. They exercise normal transitions, saved preferences, watcher restart/crash recovery, unavailable settings services, independent handler failures, corrupt state, direction changes, build guards, and install/uninstall rollback in temporary fixtures.

The complete generated QML passed Qt 5 `qmllint` on the phone. An offscreen Qt harness runs the actual generated orientation bindings and the real GSettings plugin using `GSETTINGS_BACKEND=memory`. The run reported five passes, including setup and cleanup, covering both landscape directions, restoration of a previously locked portrait orientation, and restoration of automatic rotation to the latest orientation.

Systemd verification passed. The tested phone emitted an existing unrelated warning about `StartLimitIntervalSec` in bluebinder's service file.

## Repeating the checks

From the repository root, the portable tests are:

```sh
python3 -m unittest -v test_slider.py
sh -n install-phone.sh
```

The phone needs real Qt test binaries for the additional checks. On the tested image, `/usr/bin/qmllint` and `/usr/bin/qmltestrunner` were only launcher stubs. The test tools were downloaded and extracted into a private cache without installing system packages:

```sh
mkdir -p ~/.cache/pro1x-slider-qt-qa
cd ~/.cache/pro1x-slider-qt-qa
apt-get download qtdeclarative5-dev-tools libqt5quicktest5 qml-module-qttest
for package in ./*.deb; do dpkg-deb -x "$package" root; done
```

Return to the repository root, then run:

```sh
python3 phone_checks.py --qt-prefix ~/.cache/pro1x-slider-qt-qa/root
```

This uses an offscreen platform, software rendering, and in-memory GSettings. It does not modify the real rotation settings. The tools are only needed for validation, not for the installed service.

`phone_checks.py` reads the stock system QML and expects the pinned original hash. Run it before installing the override, or after uninstalling it. An active bind mount presents the patched file at that path and will intentionally fail the stock-file guard.

## Checks still unrecorded

- A separate manual test with the normal rotation-lock toggle enabled. This has automated QML coverage.
- App-specific layouts and external displays.
- A complete wipe/reflash followed by the included legacy setup and migration.
- Other Pro1-X units, OS builds, or devices.

The original system image is kept read-only. The checksum guard and rollback paths are part of the workaround's compatibility contract.
