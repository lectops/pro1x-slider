# Pro1-X slider: backlight and landscape lock

One systemd service for the F(x)tec Pro1-X running Ubuntu Touch: slide the keyboard open to turn on its backlight and put the screen in the useful landscape orientation. Close it to turn the light off and restore the previous rotation behavior.

This bind-mount workaround is confirmed on **one Pro1-X running Ubuntu Touch 24.04-2.2 stable**. The installer checks the exact Lomiri QML file before applying the override. See [validation and compatibility](docs/VALIDATION.md) for the tested build and the limits of the checks.

| Keyboard | Backlight | Screen |
| --- | --- | --- |
| Open | On, brightness 255 | `InvertedLandscapeOrientation`, keyboard below the display |
| Closed | Off | Previous exact-orientation setting and normal Ubuntu Touch rotation behavior |

The service starts at boot, reads the initial slider state, and handles later slide events without grabbing keyboard input. It leaves the normal `rotation-lock` preference unchanged. Backlight and rotation failures are handled separately, and a saved-state journal supports recovery after a watcher crash.

## Compatibility

- F(x)tec Pro1-X / QX1050, ARM64.
- Ubuntu Touch 24.04-2.2 stable, Ubuntu 24.04.5 base.
- Tested Lomiri package: `0.5.0+0~20260910085057.5+ubports24.04.2~1.gbp0bd07c`.
- User account `phablet`, with its normal `android_input` and `plugdev` groups.
- Python 3 and standard Ubuntu Touch utilities: `systemctl`, `systemd-analyze`, `mount`, `umount`, `mountpoint`, `runuser`, and `gsettings`.
- An existing `pro1x-backlight.service`, used as the migration baseline and rollback option. A copy and setup instructions are included under [legacy-backlight/](legacy-backlight/README.md).

The required SHA-256 of `/usr/share/lomiri/OrientedShell.qml` is:

```text
633578d225f0dd41a656b27c08e0d5cc0901e6b6267c74c26edab8823524dec8
```

Keep the checksum guard when adapting this to another build. A different QML file needs review and testing before the pinned hash changes. The installer does not install packages or require pip dependencies.

## Install

1. Download the [v0.1.0 source archive](https://github.com/lectops/pro1x-slider/archive/refs/tags/v0.1.0.tar.gz), or clone this repo. Extract/copy the whole directory onto the phone.
2. If `pro1x-backlight.service` is not installed yet, follow [the included backlight setup](legacy-backlight/README.md) first.
3. In the phone's Terminal, change into the extracted repository directory and run:

   ```sh
   sh install-phone.sh
   ```

   Enter the phone's sudo password. The helper runs `sudo python3 install.py install`; the final `install` argument is required.

4. Save open apps, then reload Lomiri as phablet, or reboot the phone:

   ```sh
   systemctl --user restart lomiri-full-greeter.service
   ```

   Restarting Lomiri closes running apps. The QML override takes effect when the new Lomiri process loads it.

5. Open and close the keyboard. Check that the light and landscape direction are correct, then inspect the service:

   ```sh
   systemctl status pro1x-slider.service
   journalctl -u pro1x-slider.service -n 30 --no-pager
   ```

The installer retains the old backlight files, records whether its service was enabled/running, disables that service, and enables `pro1x-slider.service`. It refuses to overwrite an existing slider installation. If a later install is needed, uninstall the existing version first.

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

The uninstaller restores any pending orientation setting, removes the QML bind mount and slider unit, and restores the old backlight service's recorded enabled/running state. It then removes its installation directory. If settings restoration fails because the desktop session bus is unavailable, it retains the recovery files so uninstall can be retried after logging in.

## How the bind mount works

This Lomiri build responds to the older `rotation-lock` boolean but does not use the exact `orientation-lock` setting in the way the slider needs. The patch makes the QML shell honor that existing exact-orientation setting while retaining its ordinary rotation tracking.

`install.py` verifies the original QML checksum and generates a patched copy under root-owned `/userdata/system-data/pro1x-slider/`. A bind mount places that copy over `/usr/share/lomiri/OrientedShell.qml`. The system image stays read-only, and the original file is retained under the mount.

The same service prepares that mount before the display manager starts at boot, then runs the watcher as phablet. Runtime state lives in its phablet-owned `state/` subdirectory. The watcher saves the previous setting before applying an override and removes that saved state only after restoration succeeds. It queries `SW_KEYPAD_SLIDE` on `gpio-keys`: on the tested phone, 0 is open and 1 is closed.

After an OS update, an unfamiliar QML checksum prevents the override from being mounted. The watcher keeps handling the backlight and logs the unavailable orientation override. Supporting the new build requires reviewing the patch against its QML, rather than bypassing the guard.

There is no screen-off backlight timeout. App-specific layout behavior and external displays have not been tested.

## Tests

The portable tests do not need root or access to phone hardware:

```sh
python3 -m unittest -v test_slider.py
sh -n install-phone.sh
```

They cover restoration, crash/restart recovery, handler failures, direction configuration, build guards, and installer rollback in temporary fixtures. Phone-side QML and systemd checks are described in [docs/VALIDATION.md](docs/VALIDATION.md).

For a compatibility report, include the device model, Ubuntu Touch version/channel, Lomiri package version, QML checksum, and which keyboard transition fails. The known result is one tested device/build; reports from matching builds are welcome.

## Files

| File | Purpose |
| --- | --- |
| `slider.py` | Slider reader, backlight handler, and orientation journal |
| `qml_patch.py` | Exact-build QML transform and checksum guard |
| `pro1x-slider.service` | Boot startup, mount preparation, and watcher |
| `install.py` | Install, uninstall, mount, and unmount actions |
| `install-phone.sh` | Short install entry point |
| `test_slider.py` | Portable recovery and installer tests |
| `phone_checks.py` | QML syntax, systemd, and offscreen Qt checks |
| `legacy-backlight/` | Original backlight baseline and recovery instructions |

## License

GPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the license and upstream QML attribution. This repository contains the workaround and its source; the patched full Lomiri QML file is generated from the matching file already installed on the phone.
