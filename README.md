# Pro1-X slider package

One install and one systemd service for the F(x)tec Pro1-X running Ubuntu Touch: slide the keyboard open to turn on its backlight, lock the screen to the useful landscape orientation, and hide the system on-screen keyboard while typing on hardware. Close it to turn the light off and restore the previous rotation and on-screen keyboard settings.

This bind-mount workaround is confirmed on **one Pro1-X running Ubuntu Touch 24.04-2.2 stable**. The installer checks the exact Lomiri QML file before applying the override. See [validation and compatibility](docs/VALIDATION.md) for the tested build and the limits of the checks.

| Slider | Backlight | Screen | On-screen keyboard |
| --- | --- | --- | --- |
| Open | On, brightness 255 | Landscape, keyboard below the display | Hidden; hardware typing stays available |
| Closed | Off | Previous rotation behavior | Previous keyboard preference restored |

No previous backlight setup is required. The service starts at boot, reads the initial slider state, and handles later slide events without grabbing keyboard input. It leaves the normal `rotation-lock` preference unchanged. The three handlers fail independently, and saved-state journals support recovery after a watcher crash.

## Install with one command

1. On the phone, download **[pro1x-slider-0.2.0.py](https://github.com/lectops/pro1x-slider/releases/download/v0.2.0/pro1x-slider-0.2.0.py)** from the [release page](https://github.com/lectops/pro1x-slider/releases/tag/v0.2.0).
2. Open the phone's Terminal and run:

   ```sh
   python3 ~/Downloads/pro1x-slider-0.2.0.py
   ```

Enter the phone's sudo password when prompted. The file includes all source and extracts it automatically; it does not download or install dependencies. It installs all three features together and enables them at boot. If an older version is installed, it upgrades it while keeping the selected landscape direction.

At the end, it asks whether to reload Lomiri. Save open apps before answering yes: reloading closes running apps. Answer no to reboot later using the phone's power menu. No separate setup or restart command is needed.

The download location may differ if the browser saved the file somewhere other than `~/Downloads`. A [SHA256SUMS](https://github.com/lectops/pro1x-slider/releases/download/v0.2.0/SHA256SUMS) file is also available with the release.

### Remove with one command

Keep the installer file, then run:

```sh
python3 ~/Downloads/pro1x-slider-0.2.0.py uninstall
```

It removes the package, restores the saved settings and any previously installed slider helpers, and offers to reload Lomiri.

## Compatibility

- F(x)tec Pro1-X / QX1050, ARM64.
- Ubuntu Touch 24.04-2.2 stable, Ubuntu 24.04.5 base.
- Tested Lomiri package: `0.5.0+0~20260910085057.5+ubports24.04.2~1.gbp0bd07c`.
- User account `phablet`, with its normal `android_input` and `plugdev` groups.
- Python 3 and standard Ubuntu Touch utilities: `systemctl`, `systemd-analyze`, `mount`, `umount`, `mountpoint`, `runuser`, and `gsettings`.

The required SHA-256 of `/usr/share/lomiri/OrientedShell.qml` is:

```text
633578d225f0dd41a656b27c08e0d5cc0901e6b6267c74c26edab8823524dec8
```

Keep the checksum guard when adapting this to another build. A different QML file needs review and testing before the pinned hash changes. The installer does not install packages or require pip dependencies.

## Install from source

For development, download the [v0.2.0 source archive](https://github.com/lectops/pro1x-slider/archive/refs/tags/v0.2.0.tar.gz), or clone this repo. Copy/extract the whole directory onto the phone, then run from that directory:

   ```sh
   sh install-phone.sh
   ```

The script handles sudo, installation or upgrade, and the optional interface reload. Pass `--no-reload` to skip the reload prompt. To inspect the result:

   ```sh
   systemctl status pro1x-slider.service
   journalctl -u pro1x-slider.service -n 30 --no-pager
   ```

The installer enables `pro1x-slider.service` on a fresh phone. If the old `pro1x-backlight.service` or built-in user `keypad-slide.service` exists, it records their enabled/running state and disables competing helpers automatically. Uninstall restores only what was there before. A failed upgrade attempts to reinstall the previous package.

### Landscape direction

The default is the confirmed Pro1-X direction, `InvertedLandscapeOrientation`. It can also be set explicitly as phablet:

```sh
echo InvertedLandscapeOrientation > /userdata/system-data/pro1x-slider/state/landscape-orientation
```

Close and reopen the keyboard to pick up the change. The other supported value is `LandscapeOrientation`.

### Uninstall

In the phone's Terminal:

```sh
sudo python3 /userdata/system-data/pro1x-slider/install.py uninstall
systemctl --user restart lomiri-full-greeter.service
```

The uninstaller restores pending orientation and on-screen keyboard settings, removes the QML bind mount and slider unit, and restores any previous helpers' recorded enabled/running state. It then removes its installation directory. If settings restoration fails because the desktop session bus is unavailable, it retains the recovery files so uninstall can be retried after logging in.

## How the bind mount works

This Lomiri build responds to the older `rotation-lock` boolean but does not use the exact `orientation-lock` setting in the way the slider needs. The patch makes the QML shell honor that existing exact-orientation setting while retaining its ordinary rotation tracking.

`install.py` verifies the original QML checksum and generates a patched copy under root-owned `/userdata/system-data/pro1x-slider/`. A bind mount places that copy over `/usr/share/lomiri/OrientedShell.qml`. The system image stays read-only, and the original file is retained under the mount.

The same service prepares that mount before the display manager starts at boot, then runs the watcher as phablet. Runtime state lives in its phablet-owned `state/` subdirectory. The watcher saves the previous settings before applying overrides and removes saved state only after restoration succeeds. It queries `SW_KEYPAD_SLIDE` on `gpio-keys`: on the tested phone, 0 is open and 1 is closed.

While open, `com.lomiri.keyboard.maliit stay-hidden` is set to true. Closing restores its previous value, including a pre-existing preference to keep the keyboard hidden. This suppresses the system keyboard without disabling hardware key input or changing `always-show-osk`. It was confirmed in a live browser trial.

After an OS update, an unfamiliar QML checksum prevents the override from being mounted. The watcher keeps handling the backlight and keyboard and logs the unavailable orientation override. Supporting the new build requires reviewing the patch against its QML, rather than bypassing the guard.

There is no screen-off backlight timeout. App-specific layout behavior and external displays have not been tested.

## Tests

The portable tests do not need root or access to phone hardware:

```sh
python3 -m unittest -v test_slider.py test_package.py
sh -n install-phone.sh
```

The 32 tests cover restoration, crash/restart recovery, independent handlers, fresh installs, optional migration, upgrades and rollback, and single-file extraction. Phone-side QML and systemd checks are described in [docs/VALIDATION.md](docs/VALIDATION.md).

Build the downloadable installer from this source tree with:

```sh
python3 build_installer.py
```

The installer and `SHA256SUMS` are written under `dist/`. To inspect a downloaded installer's complete source without installing anything, run it with `--extract DIRECTORY`, choosing an empty directory.

For a compatibility report, include the device model, Ubuntu Touch version/channel, Lomiri package version, QML checksum, and which keyboard transition fails. The known result is one tested device/build; reports from matching builds are welcome.

## Files

| File | Purpose |
| --- | --- |
| `slider.py` | Slider reader and independent backlight, rotation, and keyboard handlers |
| `qml_patch.py` | Exact-build QML transform and checksum guard |
| `pro1x-slider.service` | Boot startup, mount preparation, and watcher |
| `install.py` | Install, uninstall, mount, and unmount actions |
| `install-phone.sh` | Short install entry point |
| `test_slider.py` | Portable recovery and installer tests |
| `phone_checks.py` | QML syntax, systemd, and offscreen Qt checks |
| `build_installer.py` | Builds the self-contained installer and checksum file |
| `test_package.py` | Complete-source extraction and installer packaging checks |

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the license and upstream QML attribution. This repository contains the workaround and its source; the patched full Lomiri QML file is generated from the matching file already installed on the phone.
