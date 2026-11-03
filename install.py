#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Install Slider Settings and its reversible Ubuntu Touch integration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

from qml_patch import patched, TARGET, STOCK_SHA256
from slider_common import APP_ID, VERSION

BASE = Path('/userdata/system-data/pro1x-slider')
UNIT = Path('/etc/systemd/system/pro1x-slider.service')
PLATFORM_UNIT = Path('/etc/systemd/system/pro1x-slider-platform.service')
WATCHER = 'pro1x-slider.service'
PLATFORM = 'pro1x-slider-platform.service'
OLD = 'pro1x-backlight.service'
NATIVE = 'keypad-slide.service'
CLICK_NAME = APP_ID + '_' + VERSION + '_all.click'
FILES = ('slider.py', 'slider_common.py', 'qml_patch.py', 'install.py',
         WATCHER, PLATFORM, 'LICENSE', 'NOTICE')
OVERLAY = BASE / 'OrientedShell.qml'
RECORD = BASE / 'installation.json'
PLATFORM_RECORD = BASE / 'platform.json'
JOURNALS = ('saved-orientation.json', 'saved-keyboard.json')

# Executed as phablet. No privileged process traverses or writes home paths.
# Linking a completed file prevents replacement of a concurrent GUI save.
SEED_CONFIG = r"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from slider_common import config_path, default_config, load_config, save_config
base, home = Path(sys.argv[1]), Path(sys.argv[2])
path = config_path(home)
if os.path.lexists(path):
    print('preserved')
    raise SystemExit(0)
checkpoint = base / 'state/last-known-good.json'
if os.path.lexists(checkpoint):
    config = load_config(checkpoint)
    origin = 'last-known-good'
else:
    config = default_config()
    legacy = base / 'state/landscape-orientation'
    origin = 'defaults'
    if os.path.lexists(legacy):
        if legacy.is_symlink():
            raise ValueError('Refusing a symlink in the legacy direction configuration')
        with legacy.open() as stream:
            raw = stream.read(129)
        if len(raw) > 128:
            raise ValueError('Saved landscape direction is too large')
        direction = raw.strip()
        mapping = {'LandscapeOrientation': 'reversed', 'InvertedLandscapeOrientation': 'keyboard'}
        if direction not in mapping:
            raise ValueError('Invalid saved landscape direction')
        config['rotation']['direction'] = mapping[direction]
        origin = 'legacy-direction'
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
fd, temporary = tempfile.mkstemp(prefix='.installer-config-', dir=path.parent)
os.close(fd)
try:
    save_config(temporary, config)
    try:
        os.link(temporary, path)
    except FileExistsError:
        origin = 'preserved'
finally:
    os.unlink(temporary)
print(origin)
"""


def run(*command, check=True):
    result = subprocess.run(command, text=True, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError(' '.join(command) + ': ' + (result.stderr or result.stdout).strip())
    return result


def user_environment():
    """Fixed phablet paths/session; never inherit sudo's HOME or Python paths."""
    account = pwd.getpwnam('phablet')
    return ('PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME=' + account.pw_dir, 'USER=phablet', 'LOGNAME=phablet',
            'XDG_CONFIG_HOME=' + str(Path(account.pw_dir) / '.config'),
            'XDG_CACHE_HOME=' + str(Path(account.pw_dir) / '.cache'),
            'XDG_DATA_HOME=' + str(Path(account.pw_dir) / '.local/share'),
            'XDG_RUNTIME_DIR=/run/user/' + str(account.pw_uid),
            'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(account.pw_uid) + '/bus',
            'PYTHONNOUSERSITE=1', 'PYTHONSAFEPATH=1', 'PYTHONDONTWRITEBYTECODE=1')


def phablet(*command, check=True):
    return run('runuser', '-u', 'phablet', '--', '/usr/bin/env', '-i',
               *user_environment(), *command, check=check)


def native_click(*arguments, show_stdout=False):
    # Keep root credentials for package/system hooks, with the known target
    # account's HOME/XDG/session for Click's per-user cleanup and child hooks.
    # Disable Python's user site because HOME belongs to the unprivileged user.
    result = run('/usr/bin/env', '-i', *user_environment(), '/usr/bin/click', *arguments)
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    if show_stdout and result.stdout.strip():
        print(result.stdout.rstrip())
    return result


def user_systemctl(*arguments, check=True):
    return phablet('systemctl', '--user', *arguments, check=check)


def same_file():
    return OVERLAY.exists() and os.path.samefile(OVERLAY, TARGET)


def mount():
    safe_parent(BASE)
    for path in (OVERLAY, RECORD):
        safe_file(path)
    if same_file():
        return
    if patched(Path(TARGET).read_bytes()) != OVERLAY.read_bytes():
        raise RuntimeError('Installed overlay does not match this Lomiri build')
    if run('mountpoint', '-q', TARGET, check=False).returncode == 0:
        raise RuntimeError('Another mount already covers Lomiri QML')
    run('mount', '--bind', str(OVERLAY), TARGET)


def unmount():
    if same_file():
        run('umount', TARGET)


def safe_parent(parent):
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Unsafe persistent parent: ' + str(parent))


def safe_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Unsafe root-owned installation file: ' + str(path))


def write(path, content, mode=0o644):
    safe_parent(path.parent)
    if os.path.lexists(path):
        safe_file(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, data):
    write(path, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode())


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


def pending_journals():
    return any(os.path.lexists(BASE / 'state' / name) for name in JOURNALS)


def recover_pending(reader=None):
    if pending_journals():
        phablet('/usr/bin/python3', '-E', '-s', str(reader or BASE / 'slider.py'),
                '--restore-only', '--state-dir', str(BASE / 'state'))
        if pending_journals():
            raise RuntimeError('Unresolved preference recovery; installation and journals retained')


def restore_previous(record):
    errors = []
    for callback in (recover_pending, lambda: restore_services(record)):
        try:
            callback()
        except Exception as error:
            errors.append(str(error))
    if errors:
        raise RuntimeError('; '.join(errors))


def read_record():
    safe_parent(BASE)
    safe_file(RECORD)
    with RECORD.open() as stream:
        record = json.load(stream)
    if not isinstance(record, dict) or record.get('stock_sha256') != STOCK_SHA256:
        raise RuntimeError('Invalid installation receipt')
    return record


def installed_gui():
    manifests = json.loads(native_click('list', '--user=phablet', '--manifest').stdout)
    if not isinstance(manifests, list) or any(not isinstance(entry, dict) for entry in manifests):
        raise RuntimeError('Invalid Click package list')
    matches = [entry for entry in manifests if entry.get('name') == APP_ID]
    if len(matches) > 1:
        raise RuntimeError('Multiple Slider Settings registrations')
    if matches:
        version = matches[0].get('version')
        if not isinstance(version, str) or not version:
            raise RuntimeError('Invalid Slider Settings registration version')
        return version
    return None


def install_gui(package):
    # Click unpacks as clickpkg and runs registration hooks for phablet. This is
    # the native path on 24.04, where the old PackageKit/pkcon is absent.
    native_click('install', '--user=phablet', '--allow-unauthenticated', str(package), show_stdout=True)
    if installed_gui() != VERSION:
        raise RuntimeError('Slider Settings was not registered for phablet')


def remove_gui():
    if installed_gui() is not None:
        native_click('unregister', '--user=phablet', APP_ID, show_stdout=True)
        if installed_gui() is not None:
            raise RuntimeError('Slider Settings registration remains after removal')


def payloads(source):
    payload = {name: (source / name).read_bytes() for name in (*FILES, CLICK_NAME)}
    manifest = json.loads(native_click('info', str(source / CLICK_NAME)).stdout)
    if any(manifest.get(key) != value for key, value in
           (('name', APP_ID), ('version', VERSION), ('architecture', 'all'))):
        raise RuntimeError('Unexpected Slider Settings Click package identity')
    run('systemd-analyze', 'verify', '--man=no', str(source / WATCHER), str(source / PLATFORM))
    return payload


def seed_config():
    account = pwd.getpwnam('phablet')
    return phablet('/usr/bin/python3', '-I', '-c', SEED_CONFIG,
                   str(BASE), account.pw_dir).stdout.strip()


def ensure_state():
    account = pwd.getpwnam('phablet')
    state = BASE / 'state'
    if not os.path.lexists(state):
        state.mkdir(mode=0o700)
        os.chown(state, account.pw_uid, account.pw_gid)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise RuntimeError('Unsafe watcher recovery directory: ' + str(state))


def stop_package():
    if UNIT.exists():
        run('systemctl', 'disable', '--now', WATCHER)
    if PLATFORM_UNIT.exists():
        run('systemctl', 'disable', '--now', PLATFORM)
    unmount()  # Also handles the v0.1/v0.2 watcher-owned mount.


def start_package():
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', PLATFORM)
    # Boot ordering is soft; independent enablement keeps platform failures from
    # blocking the watcher or retriggering mounts during watcher restarts.
    result = run('systemctl', 'start', PLATFORM, check=False)
    if result.returncode:
        print('Overlay unavailable; backlight and keyboard management remain enabled.')
    run('systemctl', 'enable', '--now', WATCHER)
    run('systemctl', 'is-active', WATCHER)


def restore_package_services(states):
    run('systemctl', 'daemon-reload')
    for name in (PLATFORM, WATCHER):
        previous = states.get(name, {})
        if previous.get('enabled'):
            run('systemctl', 'enable', name)
        if previous.get('active'):
            run('systemctl', 'start', name)
            run('systemctl', 'is-active', name)


def retain_recovery_source(backup, payload):
    # A legacy rollback cannot interpret every newer recovery journal. Keep the
    # matching recovery reader outside BASE so a later old uninstall cannot
    # discard the only copy of it. Backups contain only public code/receipts;
    # phablet needs directory traversal to run this reader against its state.
    backup.chmod(0o755)
    recovery = backup / ('recovery-' + VERSION)
    recovery.mkdir(mode=0o755)
    recovery.chmod(0o755)
    for name in ('slider.py', 'slider_common.py', 'qml_patch.py'):
        write(recovery / name, payload[name])
    return recovery


def disable_previous(record):
    if record.get('backlight_enabled') or record.get('backlight_active'):
        run('systemctl', 'disable', '--now', OLD)
    native = record.get('keypad_slide', {})
    if native.get('enabled') or native.get('active'):
        user_systemctl('disable', '--now', NATIVE)


def apply_payload(payload, stock, overlay, record):
    for name, content in payload.items():
        write(BASE / name, content)
    write(BASE / 'OrientedShell.original.qml', stock)
    write(OVERLAY, overlay)
    record.update({'version': VERSION, 'gui_version': VERSION,
                   'click_sha256': hashlib.sha256(payload[CLICK_NAME]).hexdigest()})
    write_json(RECORD, record)
    write_json(PLATFORM_RECORD, {'schema_version': 1, 'stock_sha256': STOCK_SHA256,
                               'overlay_sha256': hashlib.sha256(overlay).hexdigest()})
    for unit in (UNIT, PLATFORM_UNIT):
        write(unit, payload[unit.name])


def remove_files():
    for path in (UNIT, PLATFORM_UNIT):
        if os.path.lexists(path):
            safe_file(path)
            path.unlink()
    run('systemctl', 'daemon-reload')
    shutil.rmtree(BASE)


def install(source):
    if os.path.lexists(RECORD):
        raise RuntimeError('Already installed; run the installer to upgrade')
    if any(os.path.lexists(path) for path in (BASE, UNIT, PLATFORM_UNIT)):
        raise RuntimeError('Existing slider installation files found; refusing to overwrite them')
    if run('systemctl', 'is-active', 'pro1x-rotation.service', check=False).returncode == 0:
        raise RuntimeError('Stop the old rotation watcher before installing')
    for parent in (BASE.parent.parent, BASE.parent, UNIT.parent):
        safe_parent(parent)
    stock = Path(TARGET).read_bytes()
    overlay = patched(stock)
    payload = payloads(source)
    if installed_gui() is not None:
        raise RuntimeError('Remove the standalone Slider Settings app before first combined installation')
    backlight = service_state(OLD)
    native = service_state(NATIVE, native_command)
    record = {'stock_sha256': STOCK_SHA256,
              'backlight_present': backlight['present'],
              'backlight_enabled': backlight['enabled'],
              'backlight_active': backlight['active'], 'keypad_slide': native}
    BASE.mkdir(mode=0o755)
    BASE.chmod(0o755)
    gui_attempted = False
    try:
        apply_payload(payload, stock, overlay, record)
        ensure_state()
        record['config_migration'] = seed_config()
        write_json(RECORD, record)
        gui_attempted = True
        install_gui(BASE / CLICK_NAME)
        disable_previous(record)
        start_package()
    except Exception as original:
        errors = []
        for callback in (stop_package, lambda: restore_previous(record),
                         remove_gui if gui_attempted else lambda: None):
            try:
                callback()
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError(str(original) + '; recovery incomplete, files retained: ' + '; '.join(errors)) from original
        remove_files()
        raise
    print('Slider Settings and backend installed. Reload Lomiri or reboot to apply interface changes.')
    print('Full removal: sudo python3 ' + str(BASE / 'install.py') + ' uninstall')


def uninstall():
    record = read_record()
    ensure_state()
    run('systemctl', 'disable', '--now', WATCHER)
    restore_previous(record)
    remove_gui()
    stop_package()
    remove_files()
    print('Slider Settings and backend removed; previous slider services restored.')
    print('Saved app preferences remain in the phablet home. Reload Lomiri or reboot.')


def upgrade(source):
    record = read_record()
    if run('systemctl', 'is-active', 'pro1x-rotation.service', check=False).returncode == 0:
        raise RuntimeError('Stop the old rotation watcher before upgrading')
    updated = record.copy()
    newly_managed_native = 'keypad_slide' not in record
    if newly_managed_native:
        updated['keypad_slide'] = service_state(NATIVE, native_command)
    ensure_state()
    stock_path = BASE / 'OrientedShell.original.qml'
    safe_file(stock_path)
    stock = stock_path.read_bytes()
    overlay = patched(stock)
    if not same_file():
        patched(Path(TARGET).read_bytes())
    payload = payloads(source)
    previous_gui = installed_gui()
    previous_click = BASE / CLICK_NAME
    if previous_gui is not None:
        if previous_gui != VERSION or not previous_click.exists():
            raise RuntimeError('Cannot safely roll back the existing Slider Settings package')
        safe_file(previous_click)
        if hashlib.sha256(previous_click.read_bytes()).hexdigest() != record.get('click_sha256'):
            raise RuntimeError('Stored Click package does not match installation receipt')
    previous_services = {WATCHER: service_state(WATCHER)}
    if PLATFORM_UNIT.exists():
        previous_services[PLATFORM] = service_state(PLATFORM)
    names = tuple(dict.fromkeys((*FILES, CLICK_NAME, 'OrientedShell.original.qml',
                                'OrientedShell.qml', 'installation.json', 'platform.json')))
    backup = Path(tempfile.mkdtemp(prefix='pro1x-slider-rollback-', dir=BASE.parent))
    backup.chmod(0o700)
    unit_backup = {}
    # State stays in place throughout upgrade/rollback. Only root-owned files
    # are backed up; unresolved phablet journals are neither copied nor removed.
    for name in names:
        old = BASE / name
        if os.path.lexists(old):
            safe_file(old)
            shutil.copy2(old, backup / name)
    for unit in (UNIT, PLATFORM_UNIT):
        if os.path.lexists(unit):
            safe_file(unit)
            unit_backup[unit] = unit.read_bytes()
    write_json(backup / 'transaction.json', {
        'schema_version': 1, 'from_version': record.get('version', 'legacy'),
        'to_version': VERSION, 'services': previous_services,
        'migrated_native': updated.get('keypad_slide') if newly_managed_native else None,
        'previous_gui_version': previous_gui,
    })
    gui_attempted = False
    try:
        stop_package()
        apply_payload(payload, stock, overlay, updated)
        updated['config_migration'] = seed_config()
        write_json(RECORD, updated)
        if previous_gui is None or (backup / CLICK_NAME).read_bytes() != payload[CLICK_NAME]:
            gui_attempted = True
            install_gui(BASE / CLICK_NAME)
        disable_previous(updated)
        start_package()
    except Exception as original:
        recovery_source = None
        recovery_error = None
        try:
            stop_package()
            if pending_journals():
                recovery_source = retain_recovery_source(backup, payload)
                try:
                    recover_pending(recovery_source / 'slider.py')
                except Exception as error:
                    recovery_error = str(error)
            for name in names:
                old, destination = backup / name, BASE / name
                if old.exists():
                    write(destination, old.read_bytes())
                elif os.path.lexists(destination):
                    safe_file(destination)
                    destination.unlink()
            for unit in (UNIT, PLATFORM_UNIT):
                if unit in unit_backup:
                    write(unit, unit_backup[unit])
                elif os.path.lexists(unit):
                    safe_file(unit)
                    unit.unlink()
            if gui_attempted:
                if previous_gui is None:
                    remove_gui()
                else:
                    install_gui(BASE / CLICK_NAME)
            if recovery_error is None:
                if newly_managed_native:
                    restore_services({'keypad_slide': updated['keypad_slide']})
                restore_package_services(previous_services)
            else:
                # Older readers ignore new ownership/phase metadata. Never let
                # one consume unresolved journals or reapply a released value.
                run('systemctl', 'daemon-reload')
        except Exception as recovery:
            raise RuntimeError(str(original) + '; rollback incomplete; recovery backup retained at '
                               + str(backup) + ': ' + str(recovery)) from original
        if recovery_error is not None:
            raise RuntimeError(str(original) + '; previous files restored; watcher remains stopped; '
                               + recovery_error + '; unresolved journals retained at '
                               + str(BASE / 'state') + '; use the retained recovery reader at '
                               + str(recovery_source) + '; service recovery receipt: '
                               + str(backup / 'transaction.json')
                               + '. Resolve recovery before running the legacy uninstaller') from original
        shutil.rmtree(backup)
        raise
    shutil.rmtree(backup)
    print('Slider Settings and backend upgraded; preferences and recovery journals retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='install',
                        choices=['install', 'uninstall', 'mount', 'unmount'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator access is required; run with sudo in the phone Terminal')
    try:
        if args.action == 'install':
            source = Path(__file__).resolve().parent
            (upgrade if os.path.lexists(RECORD) else install)(source)
        else:
            {'mount': mount, 'unmount': unmount, 'uninstall': uninstall}[args.action]()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
