# Changelog

## v0.4.1 — development

- Fix key wake remaining active when its switch was off with the patched kernel. Grab keyboard events while the display is dark and release them when it lights, using a dedicated display monitor.
- Validate the hardware keyboard identity before filtering it, release on lost display state or shutdown, and retain a working on switch when monitoring fails.
- Add regression coverage for display transitions, monitor failure, input identity, and toggle/pause cleanup.
- Include the kernel patch's GPL-2.0-only attribution and license alongside the GPL-3.0-only app sources.
- Prevent administrator-owned bytecode in the temporary extraction directory, which otherwise causes a cleanup error after a successful upgrade from the confined Terminal app.

## v0.4.0 — development

- Rename the app to **Pro1 X Tweaks**, retaining its Click identity and saved settings paths.
- Add slider-open wake, keyboard-key wake, and fingerprint wake controls to the single watcher.
- Report missing keyboard kernel support without attempting a kernel install.
- Migrate the standalone wake/fingerprint helpers, preserving their startup states for rollback and uninstall.
- Migrate schema-1 preferences to schema 2 with reversible preimages. Preserve existing choices; defaults do not add wake overrides.
- Support upgrades from a different installed app version, including stopping that version’s GUI and restoring its verified Click package on failure.
- Move the keyboard kernel patch, guarded repacker, and original helper snapshots into this repository.

## v0.3.0 — 2026-11-03

- Bundle the native confined Slider Settings app with the complete slider package.
- Configure master pause, independent features, direction, and defaults without sudo or restarts.
- Reconcile settings while open and report saved, pending, applied, unavailable, and recovery states.
- Validate app/backend preferences independently and retain a durable last accepted configuration.
- Journal ownership across crashes and preserve external changes until the next physical slider cycle.
- Separate the privileged boot-time overlay from the phablet watcher.
- Preserve legacy direction/helper baselines, with matching-reader recovery and retained rollback evidence on failure.
- Supply the phone user's fixed home/session environment to native Click hooks and show successful-command warnings for diagnosis.
- Handle QML's whole-number doubles at the GUI boundary, defer brightness saves until release, and retain save failures across refreshes.
- Report the Pro1-X backlight as on/off only and omit unsupported dimming controls; a sysfs maximum of 255 does not prove physical dimming.
- Explicitly close the native settings process before replacement, including identical-version updates, without obscuring the Terminal reload prompt.
- Expand portable coverage to 112 tests. Native acceptance details are recorded in `docs/VALIDATION.md`.

## v0.2.0 — 2026-11-03

- Install all slider features together on a fresh phone; no backlight prerequisite.
- Hide the native on-screen keyboard while open and restore its previous preference on close, with crash recovery.
- Handle previous standalone and built-in slider helpers automatically and restore their original state on uninstall.
- Upgrade an existing package while preserving its landscape direction, with rollback on a failed replacement.
- Add a single-file installer with all source embedded and an optional interface reload prompt.
- Expand automated coverage to 32 tests, including fresh installs, upgrades, keyboard recovery, and packaging.

## v0.1.0 — 2026-11-03

- Export the working Pro1-X slider service as a standalone public package.
- Combine slide-triggered backlight and exact landscape orientation in one watcher.
- Default to the confirmed `InvertedLandscapeOrientation` direction.
- Restore the previous exact-orientation setting on close or orderly shutdown, with a persistent recovery journal.
- Install an exact-build QML bind mount while keeping the system image read-only.
- Include migration and rollback through the original backlight baseline.
- Include 17 portable tests and phone-side QML verification tools.

Confirmed on one Pro1-X running Ubuntu Touch 24.04-2.2 stable. Compatibility is limited to the pinned Lomiri QML build described in the README.
