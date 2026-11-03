# SPDX-License-Identifier: GPL-3.0-only
"""Installer transactions with real temporary files and Click/systemd command fakes."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install
from slider_common import config_path, default_config, load_config, save_config


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'persistent-slider'
        self.home = self.root / 'home'
        self.home.mkdir()
        self.unit = self.root / install.WATCHER
        self.platform = self.root / install.PLATFORM
        self.original = self.root / 'stock.qml'
        self.original.write_bytes(b'Original read-only system image file')
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in install.FILES:
            shutil.copy2(Path(__file__).parent / name, self.source / name)
        (self.source / install.CLICK_NAME).write_bytes(b'tiny fixture Click payload')
        self.commands = []
        self.click_commands = []
        self.gui = None
        self.gui_loaded = False
        self.gui_active_state = 'inactive'
        self.fail_gui_stop = self.fail_gui_inspection = self.gui_stop_noop = False
        self.gui_collect_on_stop = False
        self.fail_start = self.fail_gui = self.fail_restore = self.fail_native_restore = False
        self.fail_all_starts = False
        self.fail_verify = False
        self.rotation_conflict = False
        self.platform_failure = False
        self.old_present = True
        self.native_present = False
        self.watcher_enabled = self.watcher_active = True
        self.restore_removes_journals = True
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('builtins.print'))
        for name, value in [('BASE', self.base), ('UNIT', self.unit), ('PLATFORM_UNIT', self.platform),
                            ('OVERLAY', self.base / 'OrientedShell.qml'),
                            ('RECORD', self.base / 'installation.json'),
                            ('PLATFORM_RECORD', self.base / 'platform.json'),
                            ('TARGET', str(self.original))]:
            stack.enter_context(patch.object(install, name, value))
        # Host fixtures are owned by the current user rather than phone root.
        # Independent ownership tests below exercise the actual safety guards.
        for name in ['safe_parent', 'safe_file', 'mount', 'unmount']:
            stack.enter_context(patch.object(install, name))
        stack.enter_context(patch.object(install, 'patched', return_value=b'Validated QML override'))
        stack.enter_context(patch.object(install.pwd, 'getpwnam', return_value=SimpleNamespace(
            pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(self.home))))
        stack.enter_context(patch.object(install, 'run', side_effect=self.fake_run))

    def result(self, command, code=0, stdout='', stderr=''):
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def fake_run(self, *command, check=True):
        self.commands.append(command)
        if install.GUI_UNIT in command:
            if 'show' in command:
                if self.fail_gui_inspection:
                    return self.result(command, 1, stderr='User manager unavailable')
                state = 'LoadState=' + ('loaded' if self.gui_loaded else 'not-found')
                state += '\nActiveState=' + self.gui_active_state + '\n'
                return self.result(command, 0 if self.gui_loaded else 1, state)
            if command[-2:] == ('stop', install.GUI_UNIT):
                if self.fail_gui_stop:
                    raise RuntimeError('Native GUI could not stop')
                if not self.gui_stop_noop:
                    self.gui_active_state = 'inactive'
                    if self.gui_collect_on_stop:
                        self.gui_loaded = False
                return self.result(command)
        if '/usr/bin/click' in command:
            command = ('click', *command[command.index('/usr/bin/click') + 1:])
            self.click_commands.append(command)
        if command[:2] == ('click', 'list'):
            return self.result(command, stdout=json.dumps([] if self.gui is None else
                [{'name': install.APP_ID, 'version': self.gui}]))
        if command[:2] == ('click', 'info'):
            return self.result(command, stdout=json.dumps({'name': install.APP_ID,
                'version': install.VERSION, 'architecture': 'all'}))
        if command[:2] == ('systemd-analyze', 'verify') and self.fail_verify:
            raise RuntimeError('Invalid unit payload')
        if command[:2] == ('click', 'install'):
            self.gui = install.VERSION  # Hooks may partly complete before an error.
            if self.fail_gui:
                self.fail_gui = False
                raise RuntimeError('Click hook failed')
            return self.result(command)
        if command[:2] == ('click', 'unregister'):
            self.gui = None
            return self.result(command)
        if '-c' in command:
            index = command.index('-c')
            return subprocess.run([sys.executable, '-I', *command[index:]],
                                  text=True, capture_output=True, check=check)
        if '--restore-only' in command:
            if self.fail_restore:
                raise RuntimeError('Desktop session unavailable for recovery')
            if self.restore_removes_journals:
                for name in install.JOURNALS:
                    (self.base / 'state' / name).unlink(missing_ok=True)
            return self.result(command)
        if command in [('systemctl', 'enable', '--now', install.WATCHER),
                       ('systemctl', 'start', install.WATCHER)] and (self.fail_start or self.fail_all_starts):
            self.fail_start = False  # Recovery can restart the previous version.
            raise RuntimeError('New watcher could not start')
        if command == ('systemctl', 'start', install.PLATFORM) and self.platform_failure:
            return self.result(command, 1)
        if self.fail_native_restore and command[-2:] == ('start', install.NATIVE):
            raise RuntimeError('Previous native helper failed to restart')
        if 'show' in command:
            if install.WATCHER in command:
                present = self.unit.exists()
            elif install.PLATFORM in command:
                present = self.platform.exists()
            else:
                present = self.old_present if install.OLD in command else self.native_present
            return self.result(command, 0 if present else 1, 'loaded\n' if present else 'not-found\n')
        if command == ('systemctl', 'is-enabled', install.WATCHER):
            return self.result(command, 0 if self.watcher_enabled else 1,
                               'enabled\n' if self.watcher_enabled else 'disabled\n')
        if command == ('systemctl', 'is-active', install.WATCHER):
            return self.result(command, 0 if self.watcher_active else 1)
        if 'pro1x-rotation.service' in command:
            return self.result(command, 0 if self.rotation_conflict else 1)
        return self.result(command, stdout='enabled\n' if 'is-enabled' in command else '')

    def install(self):
        install.install(self.source)

    def legacy(self, version='0.2.0', direction='LandscapeOrientation'):
        self.install()
        record = json.loads(install.RECORD.read_text())
        for key in ('gui_version', 'click_sha256', 'config_migration'):
            record.pop(key)
        record['version'] = version
        if version == '0.1.0':
            record.pop('keypad_slide', None)
        install.RECORD.write_text(json.dumps(record))
        self.platform.unlink()
        (self.base / install.PLATFORM).unlink()
        (self.base / install.CLICK_NAME).unlink()
        (self.base / 'slider_common.py').unlink()
        install.PLATFORM_RECORD.unlink()
        self.gui = None
        config_path(self.home).unlink()
        (self.base / 'state/landscape-orientation').write_text(direction + '\n')
        self.unit.write_text('legacy watcher unit')
        (self.base / install.WATCHER).write_text('legacy watcher unit')

    def test_fresh_install_and_full_uninstall_preserve_image_and_preferences(self):
        self.native_present = True
        self.install()
        self.assertTrue(self.unit.exists() and self.platform.exists())
        self.assertEqual(self.gui, install.VERSION)
        self.assertEqual(load_config(config_path(self.home))['rotation']['direction'], 'keyboard')
        record = json.loads(install.RECORD.read_text())
        self.assertEqual(record['version'], '0.3.0')
        self.assertEqual(record['config_migration'], 'defaults')
        self.assertEqual(record['click_sha256'], hashlib.sha256((self.source / install.CLICK_NAME).read_bytes()).hexdigest())
        self.assertIn(('click', 'install', '--user=phablet', '--allow-unauthenticated',
                       str(self.base / install.CLICK_NAME)), self.click_commands)
        self.assertIn(('systemctl', 'disable', '--now', install.OLD), self.commands)
        self.assertIn(('systemctl', 'enable', install.PLATFORM), self.commands)
        self.assertIn(('systemctl', 'enable', '--now', install.WATCHER), self.commands)
        install.uninstall()
        self.assertFalse(self.base.exists() or self.unit.exists() or self.platform.exists())
        self.assertIsNone(self.gui)
        self.assertTrue(config_path(self.home).exists())
        self.assertIn(('systemctl', 'start', install.OLD), self.commands)
        self.assertIn(('systemctl', 'disable', '--now', install.PLATFORM), self.commands)
        self.assertTrue(any(command[-2:] == ('start', install.NATIVE) for command in self.commands))
        self.assertEqual(self.original.read_bytes(), b'Original read-only system image file')
        self.assertFalse(any('remount' in arg for command in self.commands for arg in command))

    def test_no_legacy_services_are_created_when_none_existed(self):
        self.old_present = False
        self.install()
        install.uninstall()
        self.assertNotIn(('systemctl', 'start', install.OLD), self.commands)
        self.assertNotIn(('systemctl', 'enable', install.OLD), self.commands)

    def test_missing_click_or_invalid_units_fail_before_installation_changes(self):
        self.fail_verify = True
        with self.assertRaisesRegex(RuntimeError, 'Invalid unit payload'):
            self.install()
        self.assertFalse(self.base.exists())
        self.assertFalse(any('disable' in command for command in self.commands))
        self.fail_verify = False
        (self.source / install.CLICK_NAME).unlink()
        with self.assertRaises(FileNotFoundError):
            self.install()
        self.assertFalse(self.base.exists())

    def test_bad_upgrade_payload_keeps_running_installation_intact(self):
        self.install()
        before = len(self.commands)
        receipt = install.RECORD.read_bytes()
        self.fail_verify = True
        with self.assertRaisesRegex(RuntimeError, 'Invalid unit payload'):
            install.upgrade(self.source)
        self.assertFalse(any('disable' in command for command in self.commands[before:]))
        self.assertEqual(install.RECORD.read_bytes(), receipt)
        self.assertEqual(self.gui, install.VERSION)

    def test_upgrade_rejects_competing_rotation_watcher_before_stopping_backend(self):
        self.install()
        before = len(self.commands)
        receipt = install.RECORD.read_bytes()
        self.rotation_conflict = True
        with self.assertRaisesRegex(RuntimeError, 'old rotation watcher'):
            install.upgrade(self.source)
        self.assertFalse(any('disable' in command for command in self.commands[before:]))
        self.assertEqual(install.RECORD.read_bytes(), receipt)

    def test_platform_failure_does_not_block_watcher_or_gui(self):
        self.platform_failure = True
        self.install()
        self.assertTrue(self.base.exists())
        self.assertEqual(self.gui, install.VERSION)
        self.assertIn(('systemctl', 'is-active', install.WATCHER), self.commands)

    def test_failed_watcher_start_rolls_back_fresh_install(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'watcher could not start'):
            self.install()
        self.assertFalse(self.base.exists() or self.unit.exists() or self.platform.exists())
        self.assertIsNone(self.gui)
        self.assertIn(('systemctl', 'start', install.OLD), self.commands)
        self.assertTrue(config_path(self.home).exists())

    def test_partial_click_failure_is_removed_without_disabling_old_service(self):
        self.fail_gui = True
        with self.assertRaisesRegex(RuntimeError, 'Click hook failed'):
            self.install()
        self.assertIsNone(self.gui)
        self.assertFalse(self.base.exists())
        self.assertNotIn(('systemctl', 'disable', '--now', install.OLD), self.commands)

    def test_gui_only_removal_keeps_backend_and_combined_upgrade_repairs_app(self):
        self.install()
        self.gui = None  # OpenStore/system Click removal has no backend hooks.
        state_inode = (self.base / 'state').stat().st_ino
        install.upgrade(self.source)
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.gui, install.VERSION)
        self.assertEqual(state_inode, (self.base / 'state').stat().st_ino)

    def test_never_launched_gui_is_normal_and_requires_no_stop(self):
        self.install()
        install.upgrade(self.source)
        install.uninstall()
        self.assertTrue(any(install.GUI_UNIT in command and 'show' in command for command in self.commands))
        self.assertFalse(any(command[-2:] == ('stop', install.GUI_UNIT) for command in self.commands))

    def test_identical_click_upgrade_still_closes_an_old_in_memory_gui(self):
        self.install()
        self.gui_loaded, self.gui_active_state = True, 'active'
        before = len(self.commands)
        install.upgrade(self.source)
        self.assertEqual(self.gui_active_state, 'inactive')
        self.assertTrue(any(command[-2:] == ('stop', install.GUI_UNIT) for command in self.commands[before:]))
        self.assertEqual(len([command for command in self.click_commands if command[:2] == ('click', 'install')]), 1)

    def test_upgrade_stops_gui_before_backend_writes_and_native_click_replacement(self):
        self.install()
        self.gui_loaded, self.gui_active_state = True, 'active'
        (self.source / install.CLICK_NAME).write_bytes(b'replacement GUI payload')
        before = len(self.commands)
        original_write = install.write
        writes = []
        def checked_write(path, content, mode=0o644):
            self.assertEqual(self.gui_active_state, 'inactive', str(path))
            writes.append(path)
            return original_write(path, content, mode)
        with patch.object(install, 'write', side_effect=checked_write):
            install.upgrade(self.source)
        self.assertTrue(writes)
        commands = self.commands[before:]
        stop_index = next(i for i, command in enumerate(commands) if command[-2:] == ('stop', install.GUI_UNIT))
        backend_index = commands.index(('systemctl', 'disable', '--now', install.WATCHER))
        click_index = next(i for i, command in enumerate(commands)
                          if '/usr/bin/click' in command and 'install' in command)
        self.assertLess(stop_index, backend_index)
        self.assertLess(stop_index, click_index)
        stop = commands[stop_index]
        self.assertEqual(stop[:6], ('runuser', '-u', 'phablet', '--', '/usr/bin/env', '-i'))
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/' + str(os.getuid()) + '/bus', stop)
        self.assertFalse(any(install.GUI_UNIT in command and any(action in command for action in
                            ('start', 'restart', 'enable')) for command in commands))

    def test_gui_stop_failure_aborts_upgrade_before_any_installation_mutation(self):
        self.install()
        (self.base / 'state').rmdir()
        before_files = {path.name: path.read_bytes() for path in self.base.iterdir() if path.is_file()}
        self.gui_loaded, self.gui_active_state, self.fail_gui_stop = True, 'active', True
        before = len(self.commands)
        with self.assertRaisesRegex(RuntimeError, 'Native GUI could not stop'):
            install.upgrade(self.source)
        self.assertEqual(before_files, {path.name: path.read_bytes() for path in self.base.iterdir() if path.is_file()})
        self.assertFalse((self.base / 'state').exists())
        self.assertFalse(list(self.root.glob('pro1x-slider-rollback-*')))
        self.assertFalse(any('disable' in command for command in self.commands[before:]))
        self.assertFalse(any('/usr/bin/click' in command and 'install' in command
                             for command in self.commands[before:]))

    def test_gui_stop_failure_aborts_uninstall_before_backend_or_preferences_change(self):
        self.install()
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('pending baseline')
        config = config_path(self.home).read_bytes()
        self.gui_loaded, self.gui_active_state, self.fail_gui_stop = True, 'active', True
        before = len(self.commands)
        with self.assertRaisesRegex(RuntimeError, 'Native GUI could not stop'):
            install.uninstall()
        self.assertTrue(self.unit.exists() and self.platform.exists() and install.RECORD.exists())
        self.assertEqual(journal.read_text(), 'pending baseline')
        self.assertEqual(config_path(self.home).read_bytes(), config)
        self.assertFalse(any('disable' in command for command in self.commands[before:]))
        self.assertFalse(any('/usr/bin/click' in command and 'unregister' in command
                             for command in self.commands[before:]))

    def test_successful_stop_that_leaves_gui_running_aborts_before_mutation(self):
        self.install()
        self.gui_loaded, self.gui_active_state, self.gui_stop_noop = True, 'active', True
        receipt = install.RECORD.read_bytes()
        before = len(self.commands)
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            install.upgrade(self.source)
        self.assertEqual(install.RECORD.read_bytes(), receipt)
        self.assertFalse(any('disable' in command for command in self.commands[before:]))

    def test_inaccessible_user_manager_is_not_treated_as_never_launched_gui(self):
        self.fail_gui_inspection = True
        with self.assertRaisesRegex(RuntimeError, 'User manager unavailable'):
            self.install()
        self.assertFalse(self.base.exists())

    def test_collected_transient_unit_after_stop_is_success(self):
        self.gui_loaded, self.gui_active_state, self.gui_collect_on_stop = True, 'active', True
        install.stop_gui()
        self.assertFalse(self.gui_loaded)

    def test_gui_only_removal_stops_transient_service_before_unregister(self):
        self.install()
        self.gui_loaded, self.gui_active_state = True, 'active'
        before = len(self.commands)
        install.remove_gui()
        commands = self.commands[before:]
        stop = next(i for i, command in enumerate(commands) if command[-2:] == ('stop', install.GUI_UNIT))
        unregister = next(i for i, command in enumerate(commands)
                          if '/usr/bin/click' in command and 'unregister' in command)
        self.assertLess(stop, unregister)
        self.assertEqual(self.gui_active_state, 'inactive')
        self.assertTrue(self.base.exists() and self.unit.exists())

    def test_direct_native_click_mutations_abort_when_loaded_gui_cannot_stop(self):
        self.install()
        self.gui_loaded, self.gui_active_state, self.fail_gui_stop = True, 'active', True
        for operation in (lambda: install.install_gui(self.source / install.CLICK_NAME), install.remove_gui):
            with self.subTest(operation=operation):
                before = len(self.click_commands)
                with self.assertRaisesRegex(RuntimeError, 'Native GUI could not stop'):
                    operation()
                self.assertEqual(self.click_commands[before:], [])

    def test_upgrade_preserves_accepted_config_checkpoint_and_journals(self):
        self.install()
        config = load_config(config_path(self.home))
        config['backlight']['brightness_percent'] = 37
        save_config(config_path(self.home), config)
        save_config(self.base / 'state/last-known-good.json', config)
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('{"stay-hidden":"false"}')
        before = {path.name: path.read_bytes() for path in (self.base / 'state').iterdir()}
        install.upgrade(self.source)
        self.assertEqual(load_config(config_path(self.home)), config)
        self.assertEqual(before, {path.name: path.read_bytes() for path in (self.base / 'state').iterdir()})

    def test_v01_and_v02_direction_migration(self):
        for version, direction, expected in [('0.1.0', 'LandscapeOrientation', 'reversed'),
                                              ('0.2.0', 'InvertedLandscapeOrientation', 'keyboard')]:
            with self.subTest(version=version):
                if self.base.exists():
                    install.uninstall()
                self.legacy(version, direction)
                journal = self.base / 'state/saved-orientation.json'
                journal.write_text('{"orientation-lock":"none"}')
                install.upgrade(self.source)
                self.assertEqual(load_config(config_path(self.home))['rotation']['direction'], expected)
                self.assertTrue(journal.exists())
                self.assertEqual(json.loads(install.RECORD.read_text())['config_migration'], 'legacy-direction')

    def test_missing_config_seeds_last_good_before_legacy_or_defaults(self):
        self.install()
        good = default_config()
        good['backlight']['brightness_percent'] = 21
        save_config(self.base / 'state/last-known-good.json', good)
        config_path(self.home).unlink()
        (self.base / 'state/landscape-orientation').write_text('LandscapeOrientation')
        install.upgrade(self.source)
        self.assertEqual(load_config(config_path(self.home)), good)

    def test_v01_upgrade_captures_native_helper_and_full_removal_restores_it(self):
        self.legacy('0.1.0')
        self.native_present = True
        install.upgrade(self.source)
        record = json.loads(install.RECORD.read_text())
        self.assertTrue(record['keypad_slide']['active'])
        self.assertTrue(any(command[-3:] == ('disable', '--now', install.NATIVE) for command in self.commands))
        install.uninstall()
        self.assertTrue(any(command[-2:] == ('start', install.NATIVE) for command in self.commands))

    def test_actual_v01_receipt_without_version_or_native_fields_uses_legacy_default(self):
        self.legacy('0.1.0')
        receipt = json.loads(install.RECORD.read_text())
        install.RECORD.write_text(json.dumps({key: receipt[key] for key in
            ('stock_sha256', 'backlight_enabled', 'backlight_active')}))
        (self.base / 'state/landscape-orientation').unlink()
        self.native_present = True
        install.upgrade(self.source)
        self.assertEqual(load_config(config_path(self.home))['rotation']['direction'], 'keyboard')
        updated = json.loads(install.RECORD.read_text())
        self.assertEqual(updated['version'], install.VERSION)
        self.assertTrue(updated['keypad_slide']['enabled'])
        install.uninstall()
        self.assertIn(('systemctl', 'start', install.OLD), self.commands)
        self.assertTrue(any(command[-2:] == ('start', install.NATIVE) for command in self.commands))

    def test_oversized_legacy_direction_is_retained_and_does_not_seed_partial_value(self):
        self.legacy('0.1.0')
        legacy = self.base / 'state/landscape-orientation'
        raw = 'LandscapeOrientation' + ' ' * 128 + 'invalid tail'
        legacy.write_text(raw)
        with self.assertRaises(subprocess.CalledProcessError):
            install.upgrade(self.source)
        self.assertEqual(legacy.read_text(), raw)
        self.assertFalse(config_path(self.home).exists())

    def test_failed_v01_upgrade_restores_newly_managed_native_helper(self):
        self.legacy('0.1.0')
        self.native_present = True
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'watcher could not start'):
            install.upgrade(self.source)
        self.assertTrue(any(command[-2:] == ('start', install.NATIVE) for command in self.commands))
        self.assertNotIn('keypad_slide', json.loads(install.RECORD.read_text()))

    def test_v01_failed_recovery_retains_native_receipt_and_leaves_helpers_stopped(self):
        self.legacy('0.1.0')
        self.native_present = True
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('{"stay-hidden":"false"}')
        self.fail_start = self.fail_restore = True
        before = len(self.commands)
        with self.assertRaisesRegex(RuntimeError, 'watcher remains stopped'):
            install.upgrade(self.source)
        commands = self.commands[before:]
        self.assertFalse(any(command[-2:] == ('start', install.NATIVE) for command in commands))
        backup = next(self.root.glob('pro1x-slider-rollback-*'))
        receipt = json.loads((backup / 'transaction.json').read_text())
        self.assertTrue(receipt['migrated_native']['active'])
        self.assertTrue(journal.exists())

    def test_invalid_existing_config_and_last_good_are_preserved(self):
        self.install()
        config_path(self.home).write_text('incomplete GUI write')
        checkpoint = self.base / 'state/last-known-good.json'
        good = default_config()
        save_config(checkpoint, good)
        install.upgrade(self.source)
        self.assertEqual(config_path(self.home).read_text(), 'incomplete GUI write')
        self.assertEqual(load_config(checkpoint), good)

    def test_existing_config_symlink_is_preserved_without_writing_its_target(self):
        target = self.root / 'keep.json'
        target.write_text('unrelated user data')
        path = config_path(self.home)
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        self.install()
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), 'unrelated user data')

    def test_unreadable_checkpoint_aborts_upgrade_and_retains_it(self):
        self.install()
        config_path(self.home).unlink()
        checkpoint = self.base / 'state/last-known-good.json'
        checkpoint.write_text('broken recovery data')
        with self.assertRaises(subprocess.CalledProcessError):
            install.upgrade(self.source)
        self.assertEqual(checkpoint.read_text(), 'broken recovery data')
        self.assertFalse(config_path(self.home).exists())
        self.assertTrue(self.unit.exists())

    def test_failed_legacy_upgrade_restores_exact_code_units_and_journals(self):
        self.legacy()
        old_code = (self.base / 'slider.py').read_bytes()
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('{"stay-hidden":"false"}')
        self.fail_restore = True
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'watcher could not start'):
            install.upgrade(self.source)
        self.assertEqual((self.base / 'slider.py').read_bytes(), old_code)
        self.assertEqual(self.unit.read_text(), 'legacy watcher unit')
        self.assertFalse(self.platform.exists())
        self.assertIsNone(self.gui)
        self.assertTrue(journal.exists())
        self.assertEqual(json.loads(install.RECORD.read_text())['version'], '0.2.0')
        backups = list(self.root.glob('pro1x-slider-rollback-*'))
        self.assertEqual(len(backups), 1)
        recovery = backups[0] / ('recovery-' + install.VERSION)
        self.assertEqual((recovery / 'slider.py').read_bytes(), (self.source / 'slider.py').read_bytes())
        self.assertEqual((recovery / 'slider_common.py').read_bytes(), (self.source / 'slider_common.py').read_bytes())
        self.assertEqual(stat.S_IMODE(recovery.stat().st_mode), 0o755)
        restore_command = next(command for command in self.commands if '--restore-only' in command)
        self.assertIn(str(recovery / 'slider.py'), restore_command)
        self.assertNotIn(('systemctl', 'start', install.WATCHER), self.commands)

    def test_failed_v03_upgrade_restores_stored_click_payload(self):
        self.install()
        self.gui_loaded, self.gui_active_state = True, 'active'
        before = len(self.commands)
        prior = (self.base / install.CLICK_NAME).read_bytes()
        (self.source / install.CLICK_NAME).write_bytes(b'new Click payload')
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'watcher could not start'):
            install.upgrade(self.source)
        self.assertEqual((self.base / install.CLICK_NAME).read_bytes(), prior)
        self.assertEqual(self.gui, install.VERSION)
        self.assertEqual(len([c for c in self.click_commands if c[:2] == ('click', 'install')]), 3)
        self.assertEqual(self.gui_active_state, 'inactive')
        self.assertFalse(any(install.GUI_UNIT in command and any(action in command for action in
                            ('start', 'restart', 'enable')) for command in self.commands[before:]))

    def test_failed_upgrade_restores_previously_disabled_watcher_state(self):
        self.install()
        self.watcher_enabled = self.watcher_active = False
        before = len(self.commands)
        self.fail_gui = True
        (self.source / install.CLICK_NAME).write_bytes(b'changed GUI')
        with self.assertRaisesRegex(RuntimeError, 'Click hook failed'):
            install.upgrade(self.source)
        commands = self.commands[before:]
        self.assertNotIn(('systemctl', 'enable', install.WATCHER), commands)
        self.assertNotIn(('systemctl', 'start', install.WATCHER), commands)

    def test_failed_rollback_retains_root_backup_and_live_recovery_data(self):
        self.install()
        self.fail_all_starts = True
        with self.assertRaisesRegex(RuntimeError, 'rollback incomplete; recovery backup retained'):
            install.upgrade(self.source)
        backups = list(self.root.glob('pro1x-slider-rollback-*'))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / 'installation.json').exists())
        self.assertFalse((backups[0] / 'state').exists())
        self.assertTrue((self.base / 'state').exists())

    def test_failed_uninstall_keeps_code_receipts_and_journals(self):
        self.native_present = True
        self.install()
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('{"stay-hidden":"false"}')
        self.fail_restore = self.fail_native_restore = True
        with self.assertRaisesRegex(RuntimeError, 'recovery'):
            install.uninstall()
        self.assertTrue(journal.exists() and install.RECORD.exists() and self.unit.exists())
        self.assertTrue(any('--restore-only' in command for command in self.commands))
        self.assertTrue(any(command[-2:] == ('start', install.NATIVE) for command in self.commands))

    def test_success_exit_with_unresolved_journal_still_retains_recovery_data(self):
        self.install()
        journal = self.base / 'state/saved-keyboard.json'
        journal.write_text('unresolved')
        self.restore_removes_journals = False
        with self.assertRaisesRegex(RuntimeError, 'Unresolved preference recovery'):
            install.uninstall()
        self.assertTrue(journal.exists() and self.base.exists())

    def test_seed_command_has_phablet_identity_and_clean_home_environment(self):
        self.install()
        command = next(command for command in self.commands if '-c' in command)
        self.assertEqual(command[:7], ('runuser', '-u', 'phablet', '--', '/usr/bin/env', '-i',
            'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'))
        self.assertIn('HOME=' + str(self.home), command)
        self.assertIn('-I', command)
        self.assertEqual(config_path(self.home).stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(config_path(self.home).stat().st_mode), 0o600)

    def test_root_click_uses_fixed_account_session_and_ignores_sudo_environment(self):
        with patch.dict(os.environ, {'HOME': '/root', 'XDG_CACHE_HOME': '/root/.cache',
                                     'DBUS_SESSION_BUS_ADDRESS': 'bad-bus',
                                     'PYTHONPATH': '/untrusted/python', 'LD_PRELOAD': '/untrusted/lib.so'}):
            self.install()
            install.uninstall()
        commands = [command for command in self.commands if '/usr/bin/click' in command]
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command[:2], ('/usr/bin/env', '-i'))
            executable = command.index('/usr/bin/click')
            environment = dict(item.split('=', 1) for item in command[2:executable])
            self.assertEqual(environment['HOME'], str(self.home))
            self.assertEqual(environment['XDG_CONFIG_HOME'], str(self.home / '.config'))
            self.assertEqual(environment['XDG_CACHE_HOME'], str(self.home / '.cache'))
            self.assertEqual(environment['XDG_DATA_HOME'], str(self.home / '.local/share'))
            self.assertEqual(environment['XDG_RUNTIME_DIR'], '/run/user/' + str(os.getuid()))
            self.assertEqual(environment['DBUS_SESSION_BUS_ADDRESS'],
                             'unix:path=/run/user/' + str(os.getuid()) + '/bus')
            self.assertEqual(environment['PYTHONNOUSERSITE'], '1')
            self.assertEqual(environment['PYTHONSAFEPATH'], '1')
            self.assertNotIn('PYTHONPATH', environment)
            self.assertNotIn('LD_PRELOAD', environment)
            self.assertNotIn('runuser', command)

    def test_successful_native_click_hook_warnings_remain_visible(self):
        result = subprocess.CompletedProcess([], 0, 'Click completed\n', 'Native hook warning\n')
        with patch.object(install, 'run', return_value=result), patch('builtins.print') as output:
            self.assertIs(install.native_click('unregister', '--user=phablet', install.APP_ID,
                                              show_stdout=True), result)
        output.assert_any_call('Native hook warning', file=sys.stderr)
        output.assert_any_call('Click completed')

    def test_click_registration_verification_still_rejects_false_success(self):
        with patch.object(install, 'native_click'), patch.object(install, 'installed_gui', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'not registered for phablet'):
                install.install_gui(self.source / install.CLICK_NAME)


class PrivilegedSafetyTests(unittest.TestCase):
    def test_root_guards_reject_symlinks_and_writable_code(self):
        for mode, uid in [(stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o666, 0),
                          (stat.S_IFREG | 0o644, 1000)]:
            with self.subTest(mode=mode, uid=uid), patch.object(Path, 'lstat',
                return_value=SimpleNamespace(st_mode=mode, st_uid=uid)):
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                    install.safe_file(Path('/trusted/receipt.json'))

    def test_watcher_restart_has_no_privileged_mount_commands(self):
        source = Path(__file__).parent
        watcher = (source / install.WATCHER).read_text()
        platform = (source / install.PLATFORM).read_text()
        self.assertNotIn('Wants=pro1x-slider-platform.service', watcher)
        self.assertIn('After=systemd-udev-trigger.service pro1x-slider-platform.service', watcher)
        self.assertNotIn('Requires=pro1x-slider-platform.service', watcher)
        self.assertNotIn('ExecStartPre=', watcher)
        self.assertNotIn('ExecStopPost=', watcher)
        self.assertNotIn('install.py', watcher)
        self.assertIn('Type=oneshot', platform)
        self.assertIn('RemainAfterExit=yes', platform)
        self.assertIn('Before=display-manager.service', platform)
        self.assertIn('install.py mount', platform)
        self.assertIn('install.py unmount', platform)


if __name__ == '__main__':
    unittest.main()
