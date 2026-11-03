# Changelog

## v0.1.0 — 2026-11-03

- Export the working Pro1-X slider service as a standalone public package.
- Combine slide-triggered backlight and exact landscape orientation in one watcher.
- Default to the confirmed `InvertedLandscapeOrientation` direction.
- Restore the previous exact-orientation setting on close or orderly shutdown, with a persistent recovery journal.
- Install an exact-build QML bind mount while keeping the system image read-only.
- Include migration and rollback through the original backlight baseline.
- Include 17 portable tests and phone-side QML verification tools.

Confirmed on one Pro1-X running Ubuntu Touch 24.04-2.2 stable. Compatibility is limited to the pinned Lomiri QML build described in the README.
