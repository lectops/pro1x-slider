#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Install a persistent, reversible bind mount and a single slider service."""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

from qml_patch import patched, TARGET, STOCK_SHA256

BASE = Path('/userdata/system-data/pro1x-slider')
UNIT = Path('/etc/systemd/system/pro1x-slider.service')
OLD = 'pro1x-backlight.service'
FILES = ('slider.py', 'qml_patch.py', 'install.py', 'pro1x-slider.service')
OVERLAY = BASE / 'OrientedShell.qml'
RECORD = BASE / 'installation.json'


def run(*command, check=True):
    result = subprocess.run(command, text=True, capture_output=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError(' '.join(command) + ': ' + (result.stderr or result.stdout).strip())
    return result


def same_file():
    return OVERLAY.exists() and os.path.samefile(OVERLAY, TARGET)


def mount():
    if same_file():
        return
    stock = Path(TARGET).read_bytes()
    if patched(stock) != OVERLAY.read_bytes():
        raise RuntimeError('Installed overlay does not match this Lomiri build')
    if run('mountpoint', '-q', TARGET, check=False).returncode == 0:
        raise RuntimeError('Another mount already covers Lomiri QML')
    run('mount', '--bind', str(OVERLAY), TARGET)


def unmount():
    if same_file():
        run('umount', TARGET)


def write(path, content, mode=0o644):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(content)
    temporary.chmod(mode)
    temporary.replace(path)


def restore_old(record):
    if record['backlight_enabled']:
        run('systemctl', 'enable', OLD)
    if record['backlight_active']:
        run('systemctl', 'start', OLD)


def recover_pending():
    if (BASE / 'state/saved-orientation.json').exists():
        run('runuser', '-u', 'phablet', '--', '/usr/bin/python3', str(BASE / 'slider.py'),
            '--restore-only', '--state-dir', str(BASE / 'state'))


def safe_parent(parent):
    stat = parent.lstat()
    if parent.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
        raise RuntimeError('Unsafe persistent parent: ' + str(parent))


def install(source):
    if RECORD.exists():
        raise RuntimeError('Already installed. Use systemctl restart pro1x-slider or uninstall first.')
    if BASE.exists() or UNIT.exists():
        raise RuntimeError('Existing slider installation files found; refusing to overwrite them')
    if run('systemctl', 'is-active', 'pro1x-rotation.service', check=False).returncode == 0:
        raise RuntimeError('Stop the old rotation watcher before installing')
    account = pwd.getpwnam('phablet')
    # Use Ubuntu Touch persistent storage; never remount the system image writable.
    for parent in [Path('/userdata'), Path('/userdata/system-data')]:
        safe_parent(parent)
    stock = Path(TARGET).read_bytes()
    overlay = patched(stock)
    payloads = {name: (source / name).read_bytes() for name in FILES}
    record = {
        'stock_sha256': STOCK_SHA256,
        'backlight_enabled': run('systemctl', 'is-enabled', OLD, check=False).stdout.strip() == 'enabled',
        'backlight_active': run('systemctl', 'is-active', OLD, check=False).returncode == 0,
    }
    BASE.mkdir(mode=0o755)
    try:
        for name, data in payloads.items():
            write(BASE / name, data)
        write(BASE / 'OrientedShell.original.qml', stock)
        write(OVERLAY, overlay)
        write(RECORD, json.dumps(record, indent=2).encode())
        state = BASE / 'state'
        state.mkdir(mode=0o700)
        os.chown(state, account.pw_uid, account.pw_gid)
        run('systemd-analyze', 'verify', '--man=no', str(BASE / 'pro1x-slider.service'))
        mount()
        write(UNIT, payloads['pro1x-slider.service'])
        run('systemctl', 'daemon-reload')
        run('systemctl', 'disable', '--now', OLD)
        run('systemctl', 'enable', '--now', 'pro1x-slider.service')
        run('systemctl', 'is-active', 'pro1x-slider.service')
    except Exception:
        run('systemctl', 'disable', '--now', 'pro1x-slider.service', check=False)
        restore_old(record)
        recover_pending()
        unmount()
        if UNIT.exists():
            UNIT.unlink()
        run('systemctl', 'daemon-reload', check=False)
        shutil.rmtree(BASE)
        raise
    print('Combined slider service installed and active. Original backlight service retained, disabled.')
    print('The QML override loads on the next Lomiri restart or reboot.')
    print('Restarting Lomiri closes running apps. As phablet:')
    print('  systemctl --user restart lomiri-full-greeter.service')
    print('Rollback: sudo python3 ' + str(BASE / 'install.py') + ' uninstall')


def uninstall():
    record = json.loads(RECORD.read_text())
    run('systemctl', 'disable', '--now', 'pro1x-slider.service', check=False)
    restore_old(record)
    recover_pending()
    unmount()
    if UNIT.exists():
        UNIT.unlink()
    run('systemctl', 'daemon-reload')
    shutil.rmtree(BASE)
    print('Original backlight service restored. Restart Lomiri to unload the QML override.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'uninstall', 'mount', 'unmount'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator access is required; run with sudo in the phone Terminal')
    if args.action == 'install':
        install(Path(__file__).resolve().parent)
    else:
        {'mount': mount, 'unmount': unmount, 'uninstall': uninstall}[args.action]()


if __name__ == '__main__':
    main()
