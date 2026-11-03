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

The native `com.lomiri.keyboard.maliit stay-hidden` setting was then tested in a live slider-watching trial. The owner confirmed that the browser's on-screen keyboard stayed hidden while open and that the trial behavior worked. Logs recorded open/true and closed/false transitions. The previous keyboard preference was false and was restored when closed.

Version 0.2.0 added that behavior to the permanent watcher and bundled a complete fresh install into one Python file. These earlier hardware observations do not establish acceptance of the v0.3 settings app or upgraded watcher. The fresh-install and upgrade paths have fixture coverage; a physical fresh wipe/reflash has not been performed, and no second phone has been tested.

## Version 0.3 native checks

On the same phone, the required framework `ubuntu-touch-24.04-2.x`, AppArmor policy `2404.2`, Lomiri Components 1.3, and PyOtherSide 1.6.0 are present. The app imports the PyOtherSide 1.5 API. Native `click build` successfully builds the architecture-independent package without installing dependencies.

The QML app passes the real Qt 5 `qmllint`. It renders through the running phone compositor with the native `ubuntumirclient` platform. Portrait and landscape fixture captures were inspected: single/two-column layouts, wrapped text, switches, brightness, direction, and bottom controls render without horizontal overlap. These are fixed child viewports rendered natively, not an end-to-end physical rotation test. An offscreen Lomiri Components launch fails because that platform cannot create its OpenGL context; native compositor rendering is the relevant GUI check.

An unconfined `--probe` run correctly fails: its own config/cache reads and writes succeed, but the outside scratch write is also allowed and `/proc/self/attr/current` reports `unconfined`.

The registered native test package then passes under `pro1x-slider-probe.lectops_slider-settings_0.3.0 (enforce)`: own config/cache I/O succeeds and the outside write is denied. The test uses the same app/policy with an isolated package ID and throwaway filenames; it never changes real slider settings. Its registration, installation directory, and profile/cache files are absent after removal. Native cleanup emitted wrong-HOME/session-bus warnings. Production Click now receives a fixed phablet home/XDG/session environment while retaining root credentials; successful-command warnings remain visible.

The actual combined upgrade from the earlier custom/legacy installation succeeds using that corrected invocation. Both split units are active, the backend runs as phablet, all three features report available/active while open, and the main native app runs under `pro1x-slider.lectops_slider-settings_0.3.0 (enforce)`. Installed backend and GUI source bytes match the actual bundle, including both copies of the shared protocol module. Legacy direction migrates to `keyboard`; prior helper baselines remain in the receipt.

After physical close, status reports closed/idle; the LED is 0, exact orientation is `none`, `stay-hidden` is false, and both preference journals are gone. The owner confirms rotation and keyboard behavior work through open/close. GUI acceptance exposed a brightness save failure: native PyOtherSide transports QML's rounded number as a Python float. The bridge now normalizes valid whole-number brightness input before strict disk/backend validation.

The real native QML/PyOtherSide/production-bridge regression passes in isolated storage: rejected 38.5 leaves the old value and an error visible across refreshes; a subsequent rounded 38 arrives as a Python float and persists/displays 38 as an integer, clearing the error. Dragging defers saves until release. Installed acceptance of this corrected app remains pending. A new v0.3 reboot test and privileged full removal have not been performed.

## Automated checks

All 99 portable tests pass on the development Mac. The phone runs the same 99-test suite from the actual combined installer's extracted source: 98 pass and the Node-dependent QML status-display test is skipped. Coverage includes extracted-source rebuild, installer command dispatch, native hook environment, warning visibility, and QML numeric-input normalization. Tests use isolated fixtures and do not install services or change real phone settings.

Coverage includes strict configuration validation, durable accepted preferences, live brightness/direction/pause changes, external preference ownership, recovery journal phases, session loss, unavailable features, heartbeat freshness, incompatible protocols, migration from actual v0.1 receipt shapes, GUI-only removal, Click lifecycle failure, full uninstall, and rollback with unresolved recovery. The single-file extraction can reproduce its installer from embedded source and the embedded Click artifact. A placeholder Click payload is used only in portable packaging fixtures.

The complete generated QML passed Qt 5 `qmllint` on the phone. An offscreen Qt harness runs the actual generated orientation bindings and the real GSettings plugin using `GSETTINGS_BACKEND=memory`. The run reported five passes, including setup and cleanup, covering both landscape directions, restoration of a previously locked portrait orientation, and restoration of automatic rotation to the latest orientation.

Systemd verification passes for both the v0.3 watcher and platform oneshot. Watcher restart dependencies are checked to prevent privileged mount retries. The phone emits an existing unrelated warning about `StartLimitIntervalSec` in bluebinder's service file.

## Repeating the checks

From the repository root, the portable tests are:

```sh
python3 -m unittest discover -v
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

`phone_checks.py` expects the pinned stock QML hash. Before installation it reads the system file. When the bind mount is active, pass `--stock-qml /userdata/system-data/pro1x-slider/OrientedShell.original.qml` to check against the installer's saved stock copy.

For native visual QA, stage a separate memory-only fixture:

```sh
python3 tools/stage_ui_qa.py /tmp/slider-ui-qa
```

Copy that directory to the awake phone and run `python3 capture-native.py` inside it. The harness uses the current user's running Maliit display environment, isolated XDG directories, and memory GSettings. It saves four PNGs, `capture-result.json`, and `native-render.log`. Its displayed status comes from a fixture; it does not configure the real watcher. Inspect both top and bottom captures. The script also accepts `--host` with explicit SSH identity/known-host options for a development setup.

Use `--bridge-check` when staging a separate directory to exercise the production QML/PyOtherSide bridge against real files in isolated XDG storage. This checks the native numeric transport and debounced brightness save, plus persistence of a rejected-save error across refresh. Its files are separate from the installed app and it does not control hardware.

## Checks still unrecorded

- Installed brightness fix, v0.3 app suspension/closure, and reboot behavior.
- Privileged complete v0.3 removal and a fresh physical install.
- A separate manual test with the normal rotation-lock toggle enabled. This has automated QML coverage.
- App-specific layouts and external displays.
- A complete wipe/reflash followed by the fresh package install.
- Other Pro1-X units, OS builds, or devices.

The original system image is kept read-only. The checksum guard and rollback paths are part of the workaround's compatibility contract. Normal installer failures are covered by transactional rollback tests. Abrupt power loss or SIGKILL is not covered by an automatic transaction-resume engine; retained root-owned backups and recovery receipts may require manual diagnosis.
