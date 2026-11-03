# Pro1-X slider package

Slide the keyboard open to light it, turn the display the right way up over the keyboard, and hide Ubuntu Touch's on-screen keyboard. Close it to turn the light off and restore the previous rotation and keyboard preferences.

**Slider Settings** is the native Ubuntu Touch app for configuring this behavior. The complete package includes the app, background watcher, and guarded Lomiri bind-mount workaround. No previous backlight service or slider setup is required.

This workaround targets the **F(x)tec Pro1-X running Ubuntu Touch 24.04-2.2 stable**. Compatibility is limited to the exact Lomiri build below. See [validation](docs/VALIDATION.md) for recorded checks and remaining hardware tests.

| Setting | Default when open | When closed or paused |
| --- | --- | --- |
| Keyboard light | On | Off |
| Display | Landscape with the keyboard below the screen | Previous exact-orientation preference restored |
| On-screen keyboard | Hidden; hardware typing remains available | Previous visibility preference restored |

In Slider Settings you can pause automation, toggle each feature, reverse landscape direction, and restore package defaults. Changes apply while the slider is already open. The watcher keeps working when the app is closed or suspended.

**The keyboard light on this port supports on/off only.** Its `leds-gpio` driver treats every nonzero value as on, even though the older sysfs interface reports `max_brightness=255`. Slider Settings does not offer a dimming control for this device.

## Install the whole package

1. Download **[pro1x-slider-0.3.0.py](https://github.com/lectops/pro1x-slider/releases/download/v0.3.0/pro1x-slider-0.3.0.py)** from the [release page](https://github.com/lectops/pro1x-slider/releases/tag/v0.3.0) on the phone.
2. In the phone's Terminal, run:

   ```sh
   python3 ~/Downloads/pro1x-slider-0.3.0.py
   ```

Enter the phone's sudo password when prompted. This single file includes the native app and complete source; installation needs no pip packages or additional downloads. Adjust the path if your browser saved it elsewhere.

The installer enables the background integration, installs Slider Settings in the app drawer, and migrates an existing v0.1/v0.2 installation. It preserves the selected landscape direction, existing v0.3 preferences, and recovery state. Previous standalone backlight and native slider helpers are handled automatically if present.

An upgrade closes any running Slider Settings window so its next launch loads the updated code. Open the app after the Terminal step finishes.

At the end, it offers to reload Lomiri. **Save open apps first: reloading closes running apps.** Answer no to reboot later using the power menu. Then open **Slider Settings** from the app drawer. Routine preference changes need no password or restart.

The release includes [SHA256SUMS](https://github.com/lectops/pro1x-slider/releases/download/v0.3.0/SHA256SUMS). To inspect everything without installing, run the file with `--extract DIRECTORY`, choosing an empty directory.

### Remove the whole package

Keep the downloaded installer, then run:

```sh
python3 ~/Downloads/pro1x-slider-0.3.0.py uninstall
```

This removes the app and background integration, restores owned OS preferences and previous slider helpers, and offers to reload Lomiri. Your Slider Settings preferences remain in your home directory for a later reinstall. Removing only the app through Ubuntu Touch leaves the watcher using its saved preferences; rerunning the complete installer restores the app.

If you had a working backlight service before installation, uninstall restores it, so the light may still follow the slider. That is the previous service taking over. On a fresh installation with no custom backlight service, there is no old custom service to restore.

## Using Slider Settings

- **Slider automation:** pause all features and restore preferences owned by the watcher. Individual feature choices are kept.
- **Keyboard light:** turn on the light while open. Disable it to keep the light off.
- **Landscape when open:** enable exact orientation. **Keyboard** is the confirmed Pro1-X direction; **Reversed** rotates it the other way.
- **Hide on-screen keyboard:** suppress the system keyboard while open, including in the browser.
- **Restore defaults:** enable all features and select Keyboard direction. This does not reset normal Ubuntu Touch rotation or keyboard preferences.

The status panel distinguishes saved preferences from settings the watcher has applied. A stopped watcher, stale report, unsupported feature, or pending interface reload is shown explicitly. It does not claim success from an old status file.

If another program changes a preference while the slider is open, the watcher preserves that change and relinquishes that feature until a real close/open cycle. Same-value writes by another program cannot be distinguished. The normal `rotation-lock` preference is left unchanged.

## Compatibility

| Component | Target |
| --- | --- |
| Device | F(x)tec Pro1-X / QX1050, ARM64 |
| OS | Ubuntu Touch 24.04-2.2 stable; Ubuntu 24.04.5 base |
| Lomiri | `0.5.0+0~20260910085057.5+ubports24.04.2~1.gbp0bd07c` |
| Click framework | `ubuntu-touch-24.04-2.x` |
| AppArmor policy | `2404.2`, no optional policy groups |
| GUI | Lomiri Components 1.3 and PyOtherSide 1.5 API |
| Account | `phablet`, with normal `android_input` and `plugdev` groups |

The phone supplies Python 3, Click, Qt/QML, PyOtherSide, GSettings, systemd, and mount utilities. The installer validates package identity, the QML build, and systemd units before replacing an installation.

The required SHA-256 of `/usr/share/lomiri/OrientedShell.qml` is:

```text
633578d225f0dd41a656b27c08e0d5cc0901e6b6267c74c26edab8823524dec8
```

Keep this guard when adapting the package. A different QML file needs review and testing before its hash is accepted. After an OS update, an unfamiliar build prevents the overlay from mounting; the backlight and keyboard handlers can continue, and the app reports unavailable rotation support. Do not bypass the guard to clear that message.

Other devices require a port: their slide switch, backlight interface, orientation, shell build, and confinement framework must be checked. This is a device package, not a change to every Ubuntu Touch port.

## How it works

One long-running Python watcher runs as `phablet`. It reads `SW_KEYPAD_SLIDE` on `gpio-keys` without grabbing keyboard input. On this phone 0 means open and 1 means closed. Independent handlers reconcile light, rotation, and keyboard settings, journaling ownership before writing.

A separate root oneshot prepares the guarded QML bind mount before the display manager starts at boot. A generated file under `/userdata/system-data/pro1x-slider/` overlays the matching system QML; the system image stays read-only. Restarting the watcher cannot trigger a mount or interface reload.

The confined app writes validated preferences to `~/.config/pro1x-slider.lectops/config.json` and reads the watcher's heartbeat at `~/.cache/pro1x-slider.lectops/status.json`. It has no privileged service controls. Atomic files and opaque revision tokens distinguish saved, pending, and applied changes. The watcher retains its last accepted configuration if the preferences file is missing or invalid.

The keyboard handler uses `com.lomiri.keyboard.maliit stay-hidden`, restoring the prior value on close. It does not change `always-show-osk` or disable hardware input. Recovery journals remain outside the app sandbox in the backend's phablet-owned `state/` directory.

See the [configuration and status contract](docs/CONTRACT.md) for schema and recovery details. There is no screen-off light timeout yet. App-specific layouts and external displays have not been tested.

## Development and diagnostics

Portable checks need no root or hardware:

```sh
python3 -m unittest discover -v
sh -n install-phone.sh
```

The suite covers validation, live reconciliation, crash recovery, ownership, GUI status, migration, rollback, uninstall, and complete installer extraction. Native Qt, systemd, and confinement checks are described in [docs/VALIDATION.md](docs/VALIDATION.md).

To build from source on a matching Ubuntu Touch phone, start with an empty staging directory:

```sh
python3 build_click.py /tmp/pro1x-slider-click
click build /tmp/pro1x-slider-click
python3 build_installer.py --click-package pro1x-slider.lectops_0.3.0_all.click
```

The combined installer and checksum are written under `dist/`. Build Click on a system with the matching framework; portable test payloads do not substitute for a native build. For a source install, place the built `.click` next to `install.py`, then run `sh install-phone.sh`. `--no-reload` skips the interface reload prompt.

Useful phone-side diagnostics:

```sh
systemctl status pro1x-slider.service pro1x-slider-platform.service
journalctl -u pro1x-slider.service -n 40 --no-pager
cat ~/.cache/pro1x-slider.lectops/status.json
```

If uninstall cannot restore a setting because the desktop session is unavailable, it retains the backend and journals. Unlock/log in, then retry. If an upgrade reports a retained rollback directory, keep it and its `transaction.json`; the message names the matching recovery reader. Do not delete journals to silence an error or start a legacy watcher before recovery finishes.

For a compatibility report include device model, OS version/channel, Lomiri version, QML checksum, relevant status/log output, and the transition that fails. One matching phone and automated fixtures do not establish compatibility with other builds.

## Files

| File | Purpose |
| --- | --- |
| `slider.py`, `slider_common.py` | Watcher, handlers, validated file protocol |
| `app/`, `build_click.py` | Native app, asynchronous Python bridge, Click staging |
| `qml_patch.py` | Exact-build QML transform and checksum guard |
| `pro1x-slider.service` | Unprivileged watcher |
| `pro1x-slider-platform.service` | Root boot-time overlay oneshot |
| `install.py`, `install-phone.sh` | Install, upgrade, rollback, uninstall |
| `build_installer.py` | Single-file installer with app and complete source |
| `test_*.py`, `phone_checks.py` | Portable tests and native checks |

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The full patched Lomiri QML file is generated from the matching file already installed on the phone; this repository contains the transform and its source.
