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
| Backlight driver | `leds-gpio`, device-tree `gpio-leds`; on/off only |
| Correct open orientation | `InvertedLandscapeOrientation` |

The hardware checks were performed on 3 November 2026. The owner confirmed that the combined behavior worked and that the corrected landscape direction was right-side up over the keyboard. Closing the keyboard restored the exact-orientation setting to `none` on this installation. The normal `rotation-lock` preference remained untouched.

The combined service was enabled and active after a phone reboot. Its main process started about 11 seconds into that boot. The old backlight service was disabled, and logs showed both handlers responding to open and close events.

The native `com.lomiri.keyboard.maliit stay-hidden` setting was then tested in a live slider-watching trial. The owner confirmed that the browser's on-screen keyboard stayed hidden while open and that the trial behavior worked. Logs recorded open/true and closed/false transitions. The previous keyboard preference was false and was restored when closed.

Version 0.2.0 added that behavior to the permanent watcher and bundled a complete fresh install into one Python file. These earlier hardware observations do not establish acceptance of the v0.3 settings app or upgraded watcher. Fresh-install and upgrade paths have fixture coverage; the later clean package reinstall is recorded below. A physical wipe/reflash has not been performed, and no second phone has been tested.

## Version 0.3 native checks

On the same phone, the required framework `ubuntu-touch-24.04-2.x`, AppArmor policy `2404.2`, Lomiri Components 1.3, and PyOtherSide 1.6.0 are present. The app imports the PyOtherSide 1.5 API. Native `click build` successfully builds the architecture-independent package without installing dependencies.

The QML app passes the real Qt 5 `qmllint`. It renders through the running phone compositor with the native `ubuntumirclient` platform. Portrait and landscape fixture captures were inspected: single/two-column layouts, wrapped text, switches, brightness, direction, and bottom controls render without horizontal overlap. These are fixed child viewports rendered natively, not an end-to-end physical rotation test. An offscreen Lomiri Components launch fails because that platform cannot create its OpenGL context; native compositor rendering is the relevant GUI check.

An unconfined `--probe` run correctly fails: its own config/cache reads and writes succeed, but the outside scratch write is also allowed and `/proc/self/attr/current` reports `unconfined`.

The registered native test package then passes under `pro1x-slider-probe.lectops_slider-settings_0.3.0 (enforce)`: own config/cache I/O succeeds and the outside write is denied. The test uses the same app/policy with an isolated package ID and throwaway filenames; it never changes real slider settings. Its registration, installation directory, and profile/cache files are absent after removal. Native cleanup emitted wrong-HOME/session-bus warnings. Production Click now receives a fixed phablet home/XDG/session environment while retaining root credentials; successful-command warnings remain visible.

The actual combined upgrade from the earlier custom/legacy installation succeeds using that corrected invocation. Both split units are active, the backend runs as phablet, all three features report available/active while open, and the main native app runs under `pro1x-slider.lectops_slider-settings_0.3.0 (enforce)`. Installed backend and GUI source bytes match the actual bundle, including both copies of the shared protocol module. Legacy direction migrates to `keyboard`; prior helper baselines remain in the receipt.

After physical close, status reports closed/idle; the LED is 0, exact orientation is `none`, `stay-hidden` is false, and both preference journals are gone. The owner confirms rotation and keyboard behavior work through open/close. GUI acceptance exposed a brightness save failure: native PyOtherSide transports QML's rounded number as a Python float. The bridge now normalizes valid whole-number brightness input before strict disk/backend validation.

The real native QML/PyOtherSide/production-bridge regression passes in isolated storage: rejected 38.5 leaves the old value and an error visible across refreshes; a subsequent rounded 38 arrives as a Python float and persists/displays 38 as an integer, clearing the error. Installed GUI saves also reach the watcher (13% stored, matching applied revision, sysfs value 33). This verifies the file/control path, not physical dimming: the owner reports unchanged intensity.

Hardware inspection corrects the earlier brightness assumption. The bound driver is `/sys/bus/platform/drivers/leds-gpio`, with device-tree `compatible=gpio-leds` and a GPIO-backed `button-backlight` node. This older kernel reports `max_brightness=255`, but the [GPIO LED driver maps zero to off and every nonzero value to on](https://github.com/torvalds/linux/blob/master/drivers/leds/leds-gpio.c). The package now reports `backlight_dimming=false`, drives full-on/off independently of retained experimental percentages, and hides unsupported dimming controls. Native portrait/landscape fixture captures of the corrected presentation pass: the light toggle and on/off-only helper are present, with no percentage or slider. The corrected complete package is installed: both backend and GUI bytes match the bundle, the live status explicitly reports no dimming, all requested feature states reconcile, and the fresh native GUI process loads the corrected files. With the slider closed, status is idle and the LED is 0. No claim of physical dimming is made.

Native Click replacement leaves an already running app process alive on this image; its normal app-stop command can also return success without terminating it. Explicitly stopping the app's transient systemd user unit terminates the stale process, and reopening loads current files. The installer now performs and verifies that step before package changes, including identical-payload upgrades. The corrected installer was then exercised on the real phone: the old GUI process is absent after upgrade, no rollback directory remains, and both new services are enabled and active. A later app launch uses a fresh native process. Ordering and failure handling are also covered in fixtures. It never auto-launches the GUI.

## Complete removal and clean package reinstall

The owner ran the v0.3 uninstaller. Inspection confirmed that the Click registration, installation directory, and both package units were gone, and the system QML had its pinned stock checksum. The pre-existing standalone `pro1x-backlight.service` was enabled/running again, as recorded by the original installation receipt. Its continued light handling was restoration of the old setup, not a leftover v0.3 watcher.

For a fresh-package test, the old custom backlight/rotation services and code, development stages, test apps, saved app preferences, caches, shortcuts, and old installers were then removed. The OS-supplied `keypad-slide.service` was preserved and returned to its vendor preset. SSH, Tailscale, and unrelated phone data were retained.

After restarting the phone, the owner downloaded and installed the public v0.3.0 package and confirmed that everything works. Its downloaded installer checksum matches the release:

```text
ee1a5e72db9affc9beaa2a402e9bd9f6e6747c5ac991f5e1f5a0dc57cd0064de
```

The new receipt records `backlight_present=false`, both previous backlight states false, and `config_migration=defaults`. The stock helper was present/enabled before installation and is disabled while the package manages the slider. Both package units are enabled/active. Status heartbeats advance within the freshness window, the requested revision is applied, and the native GUI runs under its enforced app profile with the same protocol module as the backend. With the slider closed, the light is 0, exact orientation is `none`, `stay-hidden` is false, and no recovery journals or rollback directories remain. The only remaining slider files in the home directory are the working app's data/launcher and the public installer retained for uninstall.

This verifies a clean package installation on the same phone after removing prior custom slider state. It is not a factory reset, OS reflash, or second-device result. The restart preceded installation; a post-install boot-start test of v0.3 remains unrecorded.

## Automated checks

All 112 portable tests pass on the development Mac. The phone runs the same 112-test suite from the actual combined installer's extracted source: 111 pass and the Node-dependent QML status-display test is skipped. Coverage includes extracted-source rebuild, installer command dispatch, native hook environment, warning visibility, and QML numeric-input normalization. Tests use isolated fixtures and do not install services or change real phone settings.

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

- A v0.3 reboot and explicit suspend/resume stress test.
- A separate manual test with the normal rotation-lock toggle enabled. This has automated QML coverage.
- App-specific layouts and external displays.
- A complete wipe/reflash followed by the fresh package install.
- Other Pro1-X units, OS builds, or devices.

The original system image is kept read-only. The checksum guard and rollback paths are part of the workaround's compatibility contract. Normal installer failures are covered by transactional rollback tests. Abrupt power loss or SIGKILL is not covered by an automatic transaction-resume engine; retained root-owned backups and recovery receipts may require manual diagnosis.
