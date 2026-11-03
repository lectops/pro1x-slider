# Original backlight baseline

These are the original backlight watcher and unit used before the combined slider service. The combined installer currently migrates this existing setup and records its enabled/running state for rollback.

If `systemctl status pro1x-backlight.service` already shows this service, use the [main install instructions](../README.md#install). Otherwise, from the root of this repository in the phone's Terminal:

```sh
mkdir -p ~/.local/lib/pro1x-backlight
cp legacy-backlight/slide-backlight.py legacy-backlight/pro1x-backlight.service ~/.local/lib/pro1x-backlight/
```

Try the original watcher first:

```sh
sudo python3 ~/.local/lib/pro1x-backlight/slide-backlight.py
```

Slide open and closed. On the tested Pro1-X, switch value 0 means open and 1 means closed; the light should turn on when opened. Press Ctrl+C to stop the foreground test, then install the baseline unit:

```sh
sudo cp ~/.local/lib/pro1x-backlight/pro1x-backlight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pro1x-backlight.service
```

Ubuntu Touch on the tested phone has persistent writable `/etc/systemd/system` storage. If that directory is read-only on another build, review that build's service layout before proceeding. This setup does not require remounting the system image writable.

Return to the [combined install instructions](../README.md#install). The new installer disables this baseline service; normal operation uses one watcher.

## Remove the baseline completely

After uninstalling the combined service, the optional original backlight setup can be removed:

```sh
sudo systemctl disable --now pro1x-backlight.service
sudo rm /etc/systemd/system/pro1x-backlight.service
sudo systemctl daemon-reload
echo 0 | sudo tee /sys/class/leds/:kbd_backlight/brightness
```

The source copies under `~/.local/lib/pro1x-backlight/` can then be removed. If an older unit was replaced during setup, restore that saved unit instead.
