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
import tempfile
import sys

from qml_patch import patched, TARGET, STOCK_SHA256

BASE = Path('/userdata/system-data/pro1x-slider')
UNIT = Path('/etc/systemd/system/pro1x-slider.service')
OLD = 'pro1x-backlight.service'
NATIVE = 'keypad-slide.service'
FILES = ('slider.py', 'qml_patch.py', 'install.py', 'pro1x-slider.service', 'LICENSE', 'NOTICE')
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


def user_systemctl(*arguments, check=True):
    uid = pwd.getpwnam('phablet').pw_uid
    return run('runuser', '-u', 'phablet', '--', '/usr/bin/env',
               'XDG_RUNTIME_DIR=/run/user/' + str(uid),
               'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(uid) + '/bus',
               'systemctl', '--user', *arguments, check=check)


def service_state(name, command=None):
    command = run if command is None else command
    result = command('systemctl', 'show', name, '-p', 'LoadState', '--value', check=False)
    load_state = result.stdout.strip()
    if load_state == 'not-found':
        return {'present': False, 'enabled': False, 'active': False}
    if result.returncode or load_state not in ('loaded', 'masked'):
        raise RuntimeError('Cannot inspect existing service: ' + name)
    return {'present': True,
            'enabled': command('systemctl', 'is-enabled', name, check=False).stdout.strip() == 'enabled',
            'active': command('systemctl', 'is-active', name, check=False).returncode == 0}


def native_command(_systemctl, *arguments, check=True):
    return user_systemctl(*arguments, check=check)


def restore_services(record):
    operations = []
    if record.get('backlight_enabled', False):
        operations.append((run, ('systemctl', 'enable', OLD)))
    if record.get('backlight_active', False):
        operations.append((run, ('systemctl', 'start', OLD)))
    native = record.get('keypad_slide', {})
    if native.get('enabled', False):
        operations.append((user_systemctl, ('enable', NATIVE)))
    if native.get('active', False):
        operations.append((user_systemctl, ('start', NATIVE)))
    errors = []
    for command, arguments in operations:
        try:
            command(*arguments)
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise RuntimeError('; '.join(errors))


def recover_pending():
    if any((BASE / 'state' / name).exists() for name in ['saved-orientation.json', 'saved-keyboard.json']):
        run('runuser', '-u', 'phablet', '--', '/usr/bin/python3', str(BASE / 'slider.py'),
            '--restore-only', '--state-dir', str(BASE / 'state'))


def restore_previous(record):
    errors = []
    for callback in [recover_pending, lambda: restore_services(record)]:
        try:
            callback()
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise RuntimeError('; '.join(errors))


def safe_parent(parent):
    stat = parent.lstat()
    if parent.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
        raise RuntimeError('Unsafe persistent parent: ' + str(parent))


def install(source, direction=None):
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
    backlight = service_state(OLD)
    native = service_state(NATIVE, native_command)
    record = {
        'stock_sha256': STOCK_SHA256,
        'backlight_present': backlight['present'],
        'backlight_enabled': backlight['enabled'],
        'backlight_active': backlight['active'],
        'keypad_slide': native,
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
        preserve_direction(direction)
        run('systemd-analyze', 'verify', '--man=no', str(BASE / 'pro1x-slider.service'))
        mount()
        write(UNIT, payloads['pro1x-slider.service'])
        run('systemctl', 'daemon-reload')
        if backlight['enabled'] or backlight['active']:
            run('systemctl', 'disable', '--now', OLD)
        if native['enabled'] or native['active']:
            user_systemctl('disable', '--now', NATIVE)
        run('systemctl', 'enable', '--now', 'pro1x-slider.service')
        run('systemctl', 'is-active', 'pro1x-slider.service')
    except Exception:
        run('systemctl', 'disable', '--now', 'pro1x-slider.service', check=False)
        restore_previous(record)
        unmount()
        if UNIT.exists():
            UNIT.unlink()
        run('systemctl', 'daemon-reload', check=False)
        shutil.rmtree(BASE)
        raise
    print('Slider package installed: backlight, landscape lock, and on-screen keyboard suppression.')
    print('Reload Lomiri or reboot to apply the interface changes.')
    print('Rollback: sudo python3 ' + str(BASE / 'install.py') + ' uninstall')


def uninstall():
    record = json.loads(RECORD.read_text())
    run('systemctl', 'disable', '--now', 'pro1x-slider.service', check=False)
    restore_previous(record)
    unmount()
    if UNIT.exists():
        UNIT.unlink()
    run('systemctl', 'daemon-reload')
    shutil.rmtree(BASE)
    print('Slider package removed; settings and any previous slider services restored.')
    print('Reload Lomiri or reboot to unload the QML override.')


def preserve_direction(direction):
    if direction is None:
        return
    account = pwd.getpwnam('phablet')
    path = BASE / 'state/landscape-orientation'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
        stream.write(direction + '\n')


def upgrade(source):
    # Validate the stored stock file and new payload before stopping the working service.
    patched((BASE / 'OrientedShell.original.qml').read_bytes())
    if not same_file():
        patched(Path(TARGET).read_bytes())
    for name in FILES:
        (source / name).read_bytes()
    config = BASE / 'state/landscape-orientation'
    direction = None
    if config.exists():
        if config.is_symlink():
            raise RuntimeError('Refusing a symlink in the direction configuration')
        direction = config.read_text().strip()
        if direction not in ('LandscapeOrientation', 'InvertedLandscapeOrientation'):
            raise RuntimeError('Invalid saved landscape direction')
    with tempfile.TemporaryDirectory(prefix='pro1x-slider-upgrade-', dir=BASE.parent) as temporary:
        backup = Path(temporary)
        for name in FILES:
            old = BASE / name
            if old.is_file():
                shutil.copy2(old, backup / name)
        uninstall()
        try:
            install(source, direction)
        except Exception:
            # Retained recovery files must be resolved before another installation.
            if BASE.exists():
                uninstall()
            run('/usr/bin/python3', str(backup / 'install.py'), 'install')
            preserve_direction(direction)
            run('systemctl', 'restart', 'pro1x-slider.service')
            raise
    print('Existing slider installation upgraded; landscape direction retained.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', default='install',
                        choices=['install', 'uninstall', 'mount', 'unmount'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator access is required; run with sudo in the phone Terminal')
    try:
        if args.action == 'install':
            source = Path(__file__).resolve().parent
            (upgrade if RECORD.exists() else install)(source)
        else:
            {'mount': mount, 'unmount': unmount, 'uninstall': uninstall}[args.action]()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print('Error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
