# Pro1 X Tweaks v0.4 contract

Approved architecture: native confined QML app, a single phablet watcher, and a separate root boot-time overlay oneshot. The installer owns privileged integration. Routine settings changes use app-scoped files and no sudo.

## Identity and paths

- Click package ID / MainView applicationName: `pro1x-slider.lectops`.
- GUI name: Pro1 X Tweaks. The Click ID, hook name, and settings directories remain stable for upgrade compatibility.
- Current backend/app version: `0.4.1`; protocol and config schema version: 2.
- Preferences: `~/.config/pro1x-slider.lectops/config.json`.
- Status: `~/.cache/pro1x-slider.lectops/status.json`.
- Root installation: `/userdata/system-data/pro1x-slider`.
- Watcher recovery state and last-known-good config remain in that installation's phablet-owned `state/`.
- Code, migration receipts, and overlay metadata remain root-owned.

The optional `home` argument to path helpers is for fixtures; otherwise honor XDG_CONFIG_HOME / XDG_CACHE_HOME and append the package ID. Neither config nor status contains executable paths, commands, arbitrary GSettings names, or installer instructions.

## Configuration

```json
{
  "schema_version": 2,
  "revision": "opaque-unique-token",
  "enabled": true,
  "backlight": {"enabled": true, "brightness_percent": 100},
  "rotation": {"enabled": true, "direction": "keyboard"},
  "keyboard": {"hide_when_open": true},
  "wake": {"slider_open": false, "keyboard": false},
  "fingerprint": {"wake_screen": true}
}
```

Allowed directions are `keyboard` (Qt InvertedLandscapeOrientation on this device) and `reversed` (Qt LandscapeOrientation). The integer 0..100 `brightness_percent` field is retained for compatibility with development builds. It has no effect on the shipped Pro1-X binary backlight profile, including when it is 0: the feature toggle controls on/off. The driver and hardware capability, not a nonzero sysfs range, determine whether dimming can be offered. Read exact schema-1 configurations by adding disabled slider/key wake and native fingerprint wake, preserving previous behavior. Reject malformed types (including bool as brightness), unknown schemas/fields, oversized files, and invalid revisions. A save uses a fresh UUID-like opaque revision token; revisions are not ordering counters.

GUI is the normal preference writer. The installer may seed or migrate config while integrating the package, using phablet ownership. The daemon validates independently, persists the last accepted config in its recovery state, and retains that config if the GUI file disappears or becomes malformed. Restore-defaults writes a new explicit config revision; it does not clear recovery journals or OS preferences.

## Shared Python module (`slider_common.py`)

- Constants: `APP_ID`, `VERSION`, `PROTOCOL_VERSION`, `SCHEMA_VERSION`.
- `default_config()` returns a new complete dict with a unique revision.
- `validate_config(value)` returns a validated copy or raises ValueError.
- `config_path(home=None)` / `status_path(home=None)` return pathlib.Path.
- `load_config(path)` reads and validates bounded JSON.
- `save_config(path, config)` atomically writes validated JSON, returning the config.
- `atomic_json_write(path, data, durable=False)` supports status/checkpoint writers.

The same module is copied into the Click package and installed backend; the app never imports host files from /userdata.

## Status (watcher is sole writer)

Required fields:

```json
{
  "schema_version": 2,
  "protocol_version": 2,
  "backend_version": "0.4.1",
  "instance_id": "unique-process-token",
  "heartbeat_monotonic": 123.0,
  "observed_revision": "opaque-unique-token",
  "applied_revision": "opaque-unique-token-or-null",
  "slider": "open",
  "state": "active",
  "config": {},
  "config_error": null,
  "capabilities": {"backlight": true, "backlight_dimming": false, "rotation": true, "keyboard": true, "slider_wake": true, "keyboard_wake": true, "fingerprint": true},
  "features": {
    "backlight": {"state": "active", "message": ""},
    "rotation": {"state": "active", "message": ""},
    "keyboard": {"state": "active", "message": ""},
    "slider_wake": {"state": "disabled", "message": ""},
    "keyboard_wake": {"state": "disabled", "message": ""},
    "fingerprint": {"state": "disabled", "message": "System fingerprint wake behavior"}
  },
  "platform": {"overlay": "mounted", "reload_required": false}
}
```

`config` is the complete accepted config. Slider: open/closed/unknown. Overall state: starting/active/paused/degraded/recovery_needed. Feature state: active/idle/disabled/waiting/error/unsupported. Platform overlay: mounted/unsupported/missing/unknown; reload_required may be null if unknown.

Echo observed_revision when validated; applied_revision only when the requested settings have reconciled successfully. A disabled/paused feature is successfully applied only after restoring its override. Unsupported requested features lead to degraded status. Status is atomic, ephemeral, and periodically refreshed; absence/staleness is never displayed as a running backend. GUI should refresh after resume and require a fresh heartbeat before trusting cached status across a launch.

`backlight_dimming` is an additive capability. The Pro1-X profile reports false; absence in an older report must not imply support. Only an explicit true in a fresh compatible report allows the GUI to offer dimming. A retained percentage is an inactive preference when this capability is false, not a requested intensity awaiting application.

## Behavior

- Config changes reconcile while already open, without service restart.
- Backlight disable/pause/close turns managed light off.
- Rotation/keyboard disable, pause, or close restores an owned saved preference.
- Changing direction keeps the original baseline until restoration.
- Journal before changing OS settings; preserve pending baselines across crashes.
- Compare actual vs last applied before restoring; preserve externally changed values and relinquish that feature for the current open cycle. Same-value external writes cannot be distinguished.
- Session loss, resume, and device reconnect invalidate applied-state caches and trigger physical state reconciliation.
- One feature failure must not prevent other application/restoration work.

## GUI bridge (`app/python/gui_bridge.py`)

- `snapshot()` returns `{config, status, backend, error, dimming_known}`. backend is checking/running/stale/missing/incompatible. Use asynchronous PyOtherSide calls.
- `save_patch(patch)` merges only known config fields, assigns a new revision, validates, atomically saves, and returns snapshot.
- QML numbers cross PyOtherSide as doubles. The bridge normalizes finite whole-number brightness values to integers before validation; fractions, booleans, non-finite values, and out-of-range values remain invalid. Disk/backend configuration stays strictly typed.
- `reset_defaults()` writes a fresh default config, then returns snapshot.
- App can show saved/applying/applied/degraded state by comparing config.revision with status.observed_revision/applied_revision. Debounce brightness changes.
- Controls include master pause, light/rotation/keyboard toggles, direction, slider-open wake, key wake, fingerprint-touch wake, status, and restore defaults. Key wake cannot be enabled through the GUI until a fresh report confirms kernel support; an already-enabled unsupported request can still be turned off. Dimming controls require confirmed capability and are absent on this Pro1-X port. No privileged service-management buttons or arbitrary command interfaces.
- Brightness saves after release and debounce. Failed-save errors remain visible across refreshes until the next intentional save/default action.

## Packaging and lifecycle

`build_click.stage_app(source_root, destination)` copies app resources, the shared module, LICENSE, and NOTICE into an empty directory. Click uses framework `ubuntu-touch-24.04-2.x` and AppArmor policy `2404.2`, without optional policy groups.

`build_installer.py` requires the built `pro1x-slider.lectops_0.4.1_all.click`. The single-file output includes that artifact and complete source/tests. Test fixtures use a placeholder Click payload only inside simulated installer tests.

Both systemd units are enabled independently. The watcher has `After=pro1x-slider-platform.service`, without a dependency that starts or retries it. Mount commands exist only in the platform oneshot. GUI changes never restart either unit.

Before changing installed files, the installer discovers the currently registered app version and explicitly stops its loaded native GUI transient service through the phablet session manager and verifies it stopped. This also applies when the package bytes/version are unchanged: an old process can otherwise retain old imported QML/Python after replacement. An unavailable/failed stop aborts before package changes; an absent unit is normal. It never automatically launches the GUI, so the Terminal reload prompt stays visible.

Upgrade preserves state in place and seeds a missing preference file as phablet: valid last accepted configuration, then legacy direction, then defaults. Existing valid schema-1 preferences are migrated, preserving choices; invalid files and symlinks are retained. Schema-2 preferences take precedence over standalone-helper defaults. A user-owned migration journal stores exact previous config/checkpoint bytes before writes. Rollback restores those bytes only if no unrecognized intervening change occurred; otherwise it retains recovery and stops rather than overwriting new choices. The journal is removed only after successful startup or rollback. Rollback stops the replacement watcher and uses its matching recovery reader before permitting a legacy watcher to start. Unresolved journals retain the backup, recovery reader, and service-state receipt for diagnosis.

Full uninstall restores owned preferences before deleting recovery data, and restores only recorded pre-existing helpers. Removing the GUI alone leaves the backend and durable accepted configuration intact.

The app's `--probe` mode tests throwaway files in its own config/cache directories and verifies denial of a harmless outside scratch write. It logs/persists the AppArmor label and result, then exits without changing real preferences or status. An unconfined run must fail; only a registered native launch under its enforced profile proves confinement.

## Wake and fingerprint integration

`hardware.py` is owned by the single unprivileged watcher. It opens the keyboard when tweaks are enabled and the AW9523B wake node reports enabled, validating its name and complete input ID. When key wake is on it reads without a grab. It emits only `KEY_WAKEUP` through uinput, after querying the compositor's current display state. Real slider-open transitions and key-down events with the slider open are eligible; startup, configuration changes, close events, key-up, autorepeat, and fingerprint events are not synthetic wake requests. A short startup delay lets the compositor discover the virtual keyboard.

When key wake is off, `keyboard_gate.py` exclusively grabs keyboard events only while both compositor ActiveOutputs counts are zero. A dedicated GLib context handles display property changes independently of slower preference reconciliation; a two-second refresh recovers missed signals. A lit output, lost display service, invalid state, pause, toggle-on, shutdown, or process exit releases the grab. Errors remain visible and prevent acknowledgment. The monitor stops before its owner closes the input descriptor. This suppresses raw keyboard wake as well as synthetic wake requests; it does not change kernel IRQ wake or controller power.

Fingerprint `wake_screen=false` takes an exclusive grab of the auxiliary `madev` input only after checking its name, complete input ID, and exact key capabilities. All its auxiliary button/gesture events are consumed; the biometric character device and templates are never opened. Setting it true, pausing tweaks, stopping the watcher, or process exit releases the grab. The setting applies independently of the slider position. It controls display wake, not sensor power or CPU interrupts.

Failures in these features are reported independently. An unavailable requested feature prevents an applied-revision acknowledgment. Missing kernel support never triggers kernel modification. The original keyboard-wake patch and guarded repacker are separate development assets in `kernel/`.

The installer records enabled/active states for existing `pro1x-wake.service` and `pro1x-fingerprint-no-wake.service` user services. During schema-1 or missing-config migration it adopts their enabled/running behavior. It disables them before the integrated watcher starts, and keeps their files for rollback or full uninstall. Subsequent upgrades preserve the original receipt. The app never starts or stops services itself.

Cross-version upgrade backs up the actual registered version's Click file and checks it against the stored receipt. Failure restores that exact package and configuration, along with newly managed helpers' previous service states. Full uninstall leaves saved app preferences but restores previously managed helper states. Kernel flashing and rollback remain separate explicit operations.
