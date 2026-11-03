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

from slider import Controller, Rotation, Keyboard, Settings, restore_settings
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
        self.rotation.target = lambda: target[0]
        self.controller.apply(True)
        self.controller.apply(False)
        target[0] = 'InvertedLandscapeOrientation'
        self.controller.apply(True)
        self.assertEqual(self.settings.value, 'InvertedLandscapeOrientation')
        self.controller.apply(False)
        self.assertEqual(self.settings.value, 'none')

    def test_mount_failure_does_not_attempt_a_write_to_the_image(self):
        with patch.object(install, 'safe_parent'), patch.object(install, 'safe_file'), patch.object(install, 'same_file', return_value=False):
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


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = FakeSettings('false')
        self.keyboard = Keyboard(self.settings, self.root / 'keyboard.json')

    def test_hide_when_open_restore_when_closed(self):
        self.keyboard.apply(True)
        self.assertEqual(self.settings.value, 'true')
        self.keyboard.apply(False)
        self.assertEqual(self.settings.value, 'false')

    def test_preexisting_hidden_preference_is_preserved(self):
        self.settings.value = 'true'
        self.keyboard.apply(True)
        self.keyboard.apply(False)
        self.assertEqual(self.settings.value, 'true')

    def test_crashed_keyboard_override_recovers_original_preference(self):
        self.keyboard.apply(True)
        restarted = Keyboard(self.settings, self.root / 'keyboard.json')
        restarted.apply(True)
        restarted.apply(False)
        self.assertEqual(self.settings.value, 'false')

    def test_keyboard_failure_does_not_block_backlight_or_rotation(self):
        self.settings.fail = True
        rotation_settings = FakeSettings('none')
        rotation = Rotation(rotation_settings, self.root / 'rotation.json', 'InvertedLandscapeOrientation')
        light = self.root / 'brightness'
        controller = Controller(rotation, light, self.keyboard)
        with self.assertLogs('pro1x-slider', 'ERROR'):
            controller.apply(True)
        self.assertEqual(light.read_text(), '255')
        self.assertEqual(rotation_settings.value, 'InvertedLandscapeOrientation')
        self.settings.fail = False
        controller.apply(True)
        self.assertEqual(self.settings.value, 'true')

    def test_rotation_restore_failure_still_restores_keyboard(self):
        rotation_settings = FakeSettings('none')
        rotation = Rotation(rotation_settings, self.root / 'rotation.json', 'InvertedLandscapeOrientation')
        rotation.apply(True)
        self.keyboard.apply(True)
        rotation_settings.fail = True
        with self.assertRaisesRegex(RuntimeError, 'rotation'):
            restore_settings(rotation, self.keyboard)
        self.assertEqual(self.settings.value, 'false')


if __name__ == '__main__':
    unittest.main()
