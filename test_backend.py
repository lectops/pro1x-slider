# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from slider_common import default_config, validate_config, save_config, load_config, MAX_JSON_BYTES
from slider import Controller, Rotation, Keyboard, ConfigStore, make_status
from test_slider import FakeSettings


class BackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = FakeSettings('PortraitOrientation')
        self.keyboard_settings = FakeSettings('false')
        self.rotation = Rotation(self.settings, self.root / 'rotation.json', 'InvertedLandscapeOrientation')
        self.keyboard = Keyboard(self.keyboard_settings, self.root / 'keyboard.json')
        self.light = self.root / 'brightness'
        self.controller = Controller(self.rotation, self.light, self.keyboard)
        self.config = default_config()

    def test_live_direction_brightness_and_disable_preserve_baseline(self):
        self.controller.apply(True, self.config)
        self.config['rotation']['direction'] = 'reversed'
        self.config['backlight']['brightness_percent'] = 30
        self.controller.apply(True, self.config)
        self.assertEqual(self.settings.value, 'LandscapeOrientation')
        self.assertEqual(self.light.read_text(), '255')
        self.config['enabled'] = False
        self.controller.apply(True, self.config)
        self.assertEqual(self.settings.value, 'PortraitOrientation')
        self.assertEqual(self.keyboard_settings.value, 'false')
        self.assertEqual(self.light.read_text(), '0')

    def test_led_maximum_and_hardware_reset_are_reconciled(self):
        self.light.with_name('max_brightness').write_text('15')
        self.config['backlight']['brightness_percent'] = 50
        self.controller.apply(True, self.config)
        self.assertEqual(self.light.read_text(), '15')
        self.light.write_text('0')
        self.controller.apply(True, self.config)
        self.assertEqual(self.light.read_text(), '15')

    def test_gpio_light_ignores_reserved_brightness_percentage(self):
        self.light.with_name('max_brightness').write_text('255')
        for percent in (0, 1, 50, 100):
            with self.subTest(percent=percent):
                self.config['backlight']['brightness_percent'] = percent
                self.assertTrue(self.controller.apply(True, self.config))
                self.assertEqual(self.light.read_text(), '255')
                self.controller.apply(False, self.config)
                self.assertEqual(self.light.read_text(), '0')
        self.controller.apply(True, self.config)
        self.config['backlight']['enabled'] = False
        self.controller.apply(True, self.config)
        self.assertEqual(self.light.read_text(), '0')
        self.config['backlight']['enabled'] = True
        self.controller.apply(True, self.config)
        self.config['enabled'] = False
        self.controller.apply(True, self.config)
        self.assertEqual(self.light.read_text(), '0')

    def test_maximum_does_not_advertise_dimming_and_legacy_zero_config_acks(self):
        self.config['backlight']['brightness_percent'] = 0
        self.light.with_name('max_brightness').write_text('255')
        self.controller.apply(True, self.config)
        store = ConfigStore(self.root / 'state', self.root / 'config.json')
        store.config = self.config
        status = make_status(store, self.controller, 'test', True)
        self.assertTrue(status['capabilities']['backlight'])
        self.assertFalse(status['capabilities']['backlight_dimming'])
        self.assertEqual(status['config']['backlight']['brightness_percent'], 0)
        self.assertEqual(status['applied_revision'], self.config['revision'])
        self.assertEqual(self.light.read_text(), '255')

    def test_external_change_relinquishes_until_physical_close(self):
        self.controller.apply(True, self.config)
        self.settings.value = 'none'
        self.controller.apply(True, self.config)
        self.config['rotation']['enabled'] = False
        self.controller.apply(True, self.config)
        self.config['rotation']['enabled'] = True
        self.controller.apply(True, self.config)
        self.assertEqual(self.settings.value, 'none')
        restarted = Rotation(self.settings, self.root / 'rotation.json', 'InvertedLandscapeOrientation')
        restarted.apply(True)
        self.assertEqual(self.settings.value, 'none')
        restarted.apply(False)
        restarted.apply(True)
        restarted.apply(False)
        self.assertEqual(self.settings.value, 'none')

    def test_external_change_immediately_before_close_is_not_clobbered(self):
        self.rotation.apply(True)
        self.settings.value = 'LandscapeOrientation'
        self.rotation.apply(False)
        self.assertEqual(self.settings.value, 'LandscapeOrientation')

    def test_crash_before_write_uses_original_baseline(self):
        with patch.object(self.settings, 'set', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.rotation.apply(True)
        restarted = Rotation(self.settings, self.root / 'rotation.json', 'LandscapeOrientation')
        restarted.apply(True)
        restarted.apply(False)
        self.assertEqual(self.settings.value, 'PortraitOrientation')

    def test_crash_after_setting_before_applied_checkpoint(self):
        original = self.rotation.persist
        def persist(phase, **fields):
            if phase == 'applied':
                raise RuntimeError('crash')
            original(phase, **fields)
        with patch.object(self.rotation, 'persist', side_effect=persist):
            with self.assertRaises(RuntimeError):
                self.rotation.apply(True)
        restarted = Rotation(self.settings, self.root / 'rotation.json', 'LandscapeOrientation')
        restarted.apply(True)
        restarted.apply(False)
        self.assertEqual(self.settings.value, 'PortraitOrientation')

    def test_legacy_recovery_preserves_original(self):
        (self.root / 'rotation.json').write_text(json.dumps({'orientation-lock': 'PortraitOrientation'}))
        self.settings.value = 'LandscapeOrientation'
        self.rotation.apply(True)
        self.rotation.apply(False)
        self.assertEqual(self.settings.value, 'PortraitOrientation')

    def test_legacy_external_value_is_preserved(self):
        (self.root / 'rotation.json').write_text(json.dumps({'orientation-lock': 'PortraitOrientation'}))
        self.settings.value = 'none'
        self.rotation.apply(False)
        self.assertEqual(self.settings.value, 'none')

    def test_config_last_known_good_survives_corruption_removal_restart(self):
        path = self.root / 'config.json'
        save_config(path, self.config)
        store = ConfigStore(self.root / 'state', path)
        store.refresh()
        path.write_text('{bad')
        store.refresh()
        self.assertIsNotNone(store.error)
        path.unlink()
        restarted = ConfigStore(self.root / 'state', path)
        self.assertEqual(restarted.refresh(), self.config)

    def test_defaults_revision_survives_restart_without_gui(self):
        store = ConfigStore(self.root / 'state', self.root / 'absent')
        first = store.refresh()
        self.assertEqual(ConfigStore(self.root / 'state', self.root / 'absent').refresh(), first)

    def test_failed_checkpoint_does_not_accept_new_config(self):
        path = self.root / 'config.json'
        store = ConfigStore(self.root / 'state', path)
        original = store.config
        save_config(path, self.config)
        with patch('slider.save_config', side_effect=OSError('disk full')):
            store.refresh()
        self.assertEqual(store.config, original)
        self.assertIn('disk full', store.error)

    def test_session_loss_recovers_and_other_features_continue(self):
        self.controller.apply(True, self.config)
        self.settings.fail = True
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(True, self.config)
        self.assertEqual(self.controller.features['rotation']['state'], 'waiting')
        self.settings.fail = False
        self.controller.apply(False, self.config)
        self.assertEqual(self.settings.value, 'PortraitOrientation')

    def test_status_acknowledges_only_successful_reconciliation(self):
        store = ConfigStore(self.root / 'state', self.root / 'absent')
        store.config = self.config
        self.controller.apply(True, self.config)
        self.assertEqual(make_status(store, self.controller, 'test', True)['applied_revision'], self.config['revision'])
        self.settings.fail = True
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(False, self.config)
        self.assertIsNone(make_status(store, self.controller, 'test', False)['applied_revision'])

    def test_disable_restores_while_input_is_disconnected(self):
        self.controller.apply(True, self.config)
        self.config['rotation']['enabled'] = False
        self.controller.apply(None, self.config)
        self.assertEqual(self.settings.value, 'PortraitOrientation')
        self.assertEqual(self.keyboard_settings.value, 'true')
        self.assertEqual(self.controller.features['keyboard']['state'], 'waiting')

    def test_reconnect_does_not_invent_a_new_cycle_after_external_change(self):
        self.controller.apply(True, self.config)
        self.settings.value = 'none'
        self.controller.apply(True, self.config)
        self.controller.apply(None, self.config)
        self.controller.apply(True, self.config)
        self.assertEqual(self.settings.value, 'none')

    def test_disabled_absent_backlight_acknowledges_without_write(self):
        self.controller.brightness = self.root / 'absent/brightness'
        self.config['backlight']['enabled'] = False
        self.assertTrue(self.controller.apply(True, self.config))
        store = ConfigStore(self.root / 'state', self.root / 'config.json')
        store.config = self.config
        status = make_status(store, self.controller, 'test', True)
        self.assertFalse(status['capabilities']['backlight'])
        self.assertEqual(status['features']['backlight']['state'], 'disabled')
        self.assertEqual(status['applied_revision'], self.config['revision'])

    def test_disappeared_lit_backlight_keeps_restoration_failure_visible(self):
        self.controller.apply(True, self.config)
        self.light.unlink()
        self.config['backlight']['enabled'] = False
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.assertFalse(self.controller.apply(True, self.config))
        self.assertFalse(self.light.exists())
        store = ConfigStore(self.root / 'state', self.root / 'config.json')
        store.config = self.config
        self.assertIsNone(make_status(store, self.controller, 'test', True)['applied_revision'])
        self.light.write_text('255')
        self.assertTrue(self.controller.apply(True, self.config))
        self.assertEqual(self.light.read_text(), '0')

    def test_enabled_rotation_without_overlay_is_not_acknowledged_when_closed(self):
        def unsupported():
            raise RuntimeError('Lomiri override is not mounted')
        self.rotation.guard = unsupported
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.assertFalse(self.controller.apply(False, self.config))
        store = ConfigStore(self.root / 'state', self.root / 'config.json')
        store.config = self.config
        status = make_status(store, self.controller, 'test', False)
        self.assertEqual(status['features']['rotation']['state'], 'unsupported')
        self.assertIsNone(status['applied_revision'])
        self.config['rotation']['enabled'] = False
        self.assertTrue(self.controller.apply(False, self.config))
        self.assertEqual(make_status(store, self.controller, 'test', False)['applied_revision'], self.config['revision'])

    def test_validation_rejects_bad_types_unknown_fields_and_versions(self):
        changes = [('schema_version', True), ('schema_version', 99), ('revision', '../bad'), ('enabled', 1), ('extra', True)]
        for key, value in changes:
            config = default_config()
            config[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_config(config)
        for value in (True, -1, 101, 1.5, '50'):
            config = default_config()
            config['backlight']['brightness_percent'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config(config)

    def test_load_rejects_duplicate_and_oversized_json(self):
        path = self.root / 'config.json'
        for content in ('{"revision":"one","revision":"two"}', ' ' * (MAX_JSON_BYTES + 1)):
            path.write_text(content)
            with self.assertRaises(ValueError):
                load_config(path)

    def test_corrupt_journal_is_recovery_needed(self):
        (self.root / 'rotation.json').write_text('broken')
        with self.assertLogs('pro1x-slider', 'ERROR'):
            self.controller.apply(True, self.config)
        store = ConfigStore(self.root / 'state', self.root / 'absent')
        self.assertEqual(make_status(store, self.controller, 'test', True)['state'], 'recovery_needed')


if __name__ == '__main__':
    unittest.main()
