# SPDX-License-Identifier: GPL-3.0-only
import json
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from slider import Controller, Rotation, Settings
import install
import qml_patch


class FakeSettings:
    def __init__(self, value='none'):
        self.value = value
        self.fail = False
        self.writes = []

    def get(self):
        if self.fail:
            raise RuntimeError('Desktop session unavailable')
        return self.value

    def set(self, value):
        if self.fail:
            raise RuntimeError('Desktop session unavailable')
        self.value = value
        self.writes.append(value)


class SliderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = self.root / 'saved.json'
        self.settings = FakeSettings()
        self.rotation = Rotation(self.settings, self.journal, 'LandscapeOrientation')
        self.light = self.root / 'brightness'
        self.controller = Controller(self.rotation, self.light)

    def test_open_close_restores_existing_orientation(self):
        self.settings.value = 'PortraitOrientation'
        self.controller.apply(True)
        self.assertEqual(self.light.read_text(), '255')
        self.assertEqual(self.settings.value, 'LandscapeOrientation')
        self.assertEqual(json.loads(self.journal.read_text())['orientation-lock'], 'PortraitOrientation')
        self.controller.apply(False)
        self.assertEqual(self.light.read_text(), '0')
        self.assertEqual(self.settings.value, 'PortraitOrientation')
        self.assertFalse(self.journal.exists())

    def test_restart_while_open_keeps_the_original_baseline(self):
        self.rotation.apply(True)
        restarted = Rotation(self.settings, self.journal, 'LandscapeOrientation')
        restarted.apply(True)
        restarted.restore()
        self.assertEqual(self.settings.value, 'none')

    def test_restart_while_closed_recovers_a_crashed_override(self):
        self.rotation.apply(True)
        restarted = Rotation(self.settings, self.journal, 'LandscapeOrientation')
        restarted.apply(False)
        self.assertEqual(self.settings.value, 'none')

    def test_backlight_works_before_session_and_rotation_retries(self):
        self.settings.fail = True
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(True)
        self.assertEqual(self.light.read_text(), '255')
        self.assertFalse(self.journal.exists())
        self.settings.fail = False
        self.controller.apply(True)
        self.assertEqual(self.settings.value, 'LandscapeOrientation')

    def test_backlight_failure_does_not_block_rotation(self):
        self.controller.brightness = self.root / 'missing/brightness'
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(True)
        self.assertEqual(self.settings.value, 'LandscapeOrientation')
        self.controller.brightness = self.light
        self.controller.apply(True)
        self.assertEqual(self.light.read_text(), '255')

    def test_failed_restore_retries_and_preserves_saved_state(self):
        self.controller.apply(True)
        self.settings.fail = True
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(False)
        self.assertEqual(self.light.read_text(), '0')
        self.assertTrue(self.journal.exists())
        self.settings.fail = False
        self.controller.apply(False)
        self.assertEqual(self.settings.value, 'none')
        self.assertFalse(self.journal.exists())

    def test_failed_close_then_reopen_reapplies_override(self):
        self.controller.apply(True)
        with patch.object(Path, 'unlink', side_effect=OSError('Read-only state directory')):
            with self.assertLogs('pro1x-slider', 'ERROR'):
                self.controller.apply(False)
        self.assertEqual(self.settings.value, 'none')
        self.controller.apply(True)
        self.assertEqual(self.settings.value, 'LandscapeOrientation')

    def test_journal_is_saved_before_settings_change(self):
        def fail_write(value):
            self.assertTrue(self.journal.exists())
            raise RuntimeError('Write failed')
        self.settings.set = fail_write
        with self.assertRaises(RuntimeError):
            self.rotation.apply(True)
        self.assertEqual(json.loads(self.journal.read_text())['orientation-lock'], 'none')

    def test_corrupt_journal_disables_only_rotation(self):
        self.journal.write_text('broken')
        rotation = Rotation(self.settings, self.journal, 'LandscapeOrientation')
        controller = Controller(rotation, self.light)
        with self.assertLogs('pro1x-slider', 'ERROR'):
            controller.apply(True)
        self.assertEqual(self.light.read_text(), '255')
        self.assertEqual(self.settings.writes, [])

    def test_no_repeated_settings_writes_on_idle_ticks(self):
        self.controller.apply(True)
        for _ in range(10):
            self.controller.apply(True)
        self.assertEqual(self.settings.writes, ['LandscapeOrientation'])

    def test_unrecognized_build_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'differs from the tested build'):
            qml_patch.patched(b'Another Lomiri release')

    def test_missing_patch_keeps_backlight_working_without_changing_settings(self):
        def absent():
            raise RuntimeError('New OS build: overlay not mounted')
        self.rotation.guard = absent
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(True)
        self.assertEqual(self.light.read_text(), '255')
        self.assertEqual(self.settings.writes, [])
        self.assertFalse(self.journal.exists())

    def test_direction_can_change_on_the_next_open(self):
        target = ['LandscapeOrientation']
        self.rotation.orientation = lambda: target[0]
        self.controller.apply(True)
        self.controller.apply(False)
        target[0] = 'InvertedLandscapeOrientation'
        self.controller.apply(True)
        self.assertEqual(self.settings.value, 'InvertedLandscapeOrientation')
        self.controller.apply(False)
        self.assertEqual(self.settings.value, 'none')

    def test_mount_failure_does_not_attempt_a_write_to_the_image(self):
        with patch.object(install, 'same_file', return_value=False):
            with patch.object(Path, 'read_bytes', return_value=b'Wrong build'):
                with patch.object(install, 'run') as run:
                    with self.assertRaises(RuntimeError):
                        install.mount()
                    run.assert_not_called()

    def test_unmount_leaves_other_overrides_untouched(self):
        with patch.object(install, 'same_file', return_value=False):
            with patch.object(install, 'run') as run:
                install.unmount()
                run.assert_not_called()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'persistent-slider'
        self.unit = self.root / 'pro1x-slider.service'
        self.original = self.root / 'stock.qml'
        self.original.write_bytes(b'Original system image file')
        self.commands = []
        self.fail_start = False
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('builtins.print'))
        for name, value in [('BASE', self.base), ('UNIT', self.unit),
                            ('OVERLAY', self.base / 'OrientedShell.qml'),
                            ('RECORD', self.base / 'installation.json'), ('TARGET', str(self.original))]:
            stack.enter_context(patch.object(install, name, value))
        for name in ['safe_parent', 'mount', 'unmount']:
            stack.enter_context(patch.object(install, name))
        stack.enter_context(patch.object(install, 'patched', return_value=b'Validated QML override'))
        stack.enter_context(patch.object(install.pwd, 'getpwnam',
                                        return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        stack.enter_context(patch.object(install, 'run', side_effect=self.fake_run))

    def fake_run(self, *command, check=True):
        self.commands.append(command)
        if command == ('systemctl', 'enable', '--now', 'pro1x-slider.service') and self.fail_start:
            raise RuntimeError('New service could not start')
        code = 1 if 'pro1x-rotation.service' in command else 0
        stdout = 'enabled\n' if 'is-enabled' in command else ''
        return subprocess.CompletedProcess(command, code, stdout, '')

    def test_install_uninstall_preserve_image_and_restore_backlight(self):
        install.install(Path(__file__).resolve().parent)
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.original.read_bytes(), b'Original system image file')
        self.assertIn(('systemctl', 'disable', '--now', install.OLD), self.commands)
        install.uninstall()
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.base.exists())
        self.assertIn(('systemctl', 'enable', install.OLD), self.commands)
        self.assertIn(('systemctl', 'start', install.OLD), self.commands)
        self.assertEqual(self.original.read_bytes(), b'Original system image file')

    def test_failed_start_rolls_back_to_original_service(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'could not start'):
            install.install(Path(__file__).resolve().parent)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.base.exists())
        self.assertEqual(self.original.read_bytes(), b'Original system image file')
        self.assertIn(('systemctl', 'enable', install.OLD), self.commands)
        self.assertIn(('systemctl', 'start', install.OLD), self.commands)


if __name__ == '__main__':
    unittest.main()
