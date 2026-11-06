# Original standalone helpers

These are the exact helper snapshots from the working 5 November 2026 experiments. They are retained for migration reference. The maintained implementation is `hardware.py`, owned by the package's single watcher.

- `wake.py` and `pro1x-wake.service`: slider-open and keyboard-open wake.
- `no-wake.py` and `pro1x-fingerprint-no-wake.service`: suppress the reader's auxiliary button events, without touching its biometric connection.

Do not install these alongside Pro1 X Tweaks. The installer records existing user-service states, adopts their behavior when migrating old preferences, and disables them before starting the integrated watcher. It leaves their original files in place so rollback and full uninstall can restore the recorded enabled/active states.
