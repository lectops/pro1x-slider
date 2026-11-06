# Optional keyboard-wake kernel

Pro1 X Tweaks does not flash a kernel. Slider-open wake and fingerprint wake control work in userspace. Waking with a hardware keyboard key also needs the keyboard driver to keep scanning while the display is off and register its interrupt as a wake source.

On the tested stock Pro1 X kernel, the AW9523B driver disables its interrupt and holds the keyboard chip in reset when the display blanks. A userspace listener alone cannot receive a key press in that state.

## Recorded build

- Device: F(x)tec Pro1 X / QX1050, Ubuntu Touch 24.04-2.2 stable, image 3.
- Stock kernel: `4.19.157-perf+`, built 19 October 2026, 14:27:39 UTC.
- [Kernel source](https://gitlab.com/ubports/porting/community-ports/android11/fxtec-pro1x/kernel-fxtec-pro1x/-/tree/ee7896a39c81df7f765b722e63184c8d142d919e): `ee7896a39c81df7f765b722e63184c8d142d919e`.
- [Adaptation source](https://gitlab.com/ubports/porting/community-ports/android11/fxtec-pro1x/fxtec-pro1x/-/tree/3e119fed31ee1dbca9a3cf92b093278104df3474): `3e119fed31ee1dbca9a3cf92b093278104df3474`.
- Stock boot image SHA-256: `31ca37e71670dbc6d27340fc0a27960738e4a1e294b9f3530f48309fe4ee0ca3`.
- Tested modified boot image SHA-256: `2a237c896aa06638033adb4b3795e68be84e0aa6fcea967e07253c45170cfb6a`.

`keyboard-wake.patch` registers the keyboard wake IRQ, preserves scanning across display blanking when wake is enabled, and schedules scans on the freezable workqueue. It preserves Caps Lock LED blank/resume handling and corrects IRQ bookkeeping. It exposes `/sys/bus/i2c/devices/0-005b/power/wakeup`; the app requires it to read `enabled` before enabling key-wake handling.

The kernel can wake the CPU even while the slider is closed. The app only emits synthetic display-wake events for key presses when the slider is open. Turning the app toggle off also blocks raw keyboard events while the display is dark, releasing them when it lights; it does not change the kernel's wake policy. Long-term battery effects have not been measured.

## Build and repack

This directory records an exact-build experiment, not a complete automated kernel toolchain. Large source archives, toolchains, boot images, and device backups are not included in Git or the app. Obtain the matching stock image and retain a verified readback of the phone's actual boot partition before any separate flashing work.

The successful build used Ubuntu Clang 11.1.0 and Android GNU binutils `2.27-41d8fcb`. Newer GNU and LLVM linkers failed on this vendor kernel. Start with the stock kernel configuration, preserve module version/signature enforcement and the `4.19.157-perf+` release string, and include the stock public module-signing certificate in the built-in trust store. The original experiment verified 163 imported kernel symbol CRCs with zero mismatches; unchanged companion-module symbols were checked separately.

`make-test-boot.py` requires Python 3.11 or newer. Its workspace must contain `stock-boot.img` and the exact AOSP packaging tools whose hashes are pinned in `TOOLS` in the script: `avbtool.py`, `mkbootimg-v2.py`, and `unpack_bootimg.py`. Tool acquisition and compiler setup are not automated here. Do not replace the hash guards just to accept different tools or images.

```sh
python3 kernel/make-test-boot.py --workspace /path/to/verified-workspace \
  --kernel /path/to/Image.gz --output /path/to/new-keyboard-wake-boot.img
```

The repacker never connects to a device. It verifies stock/tool hashes, preserves the ramdisk, device tree and boot metadata, verifies the AVB footer, and refuses an existing output. The recorded stock footer already uses algorithm `NONE`; the repacker preserves that policy.

## Device evidence and rollback

On the one tested phone, the modified image booted successfully. A letter key woke the display with the keyboard open, including after at least 90 seconds unplugged without remote queries. Slider-open wake remained working and dark-screen touches left the display off. These observations used the original standalone wake helper; the integrated package has its own validation record in `docs/VALIDATION.md`.

That bootloader did **not** support `fastboot boot`: it accepted the download and rejected the command. The test therefore used a verified backup of the actual active boot partition, flashed only that partition, then verified the complete written partition from recovery before normal boot. Recovery, userdata, and the other boot slot were unchanged.

Rollback means restoring that device's verified original boot image to the same recorded slot. The app uninstaller does not restore or flash kernels. An OS update may replace the kernel; review its build and compatibility before reapplying this patch or any old image. Do not assume the tested slot or bootloader behavior applies to another device.

## Licensing

`keyboard-wake.patch` follows the upstream kernel's GPL-2.0-only license; its text is in `LICENSE-GPL-2.0`. The upstream driver attribution is recorded in the repository's `NOTICE`. The independent `make-test-boot.py` userspace tool is GPL-3.0-only, like the app.
