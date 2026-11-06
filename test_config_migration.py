# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import tempfile
import unittest

import config_migration as migration
from slider_common import config_path, default_config, load_config, save_config, validate_config


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name) / 'package'
        self.home = Path(temporary.name) / 'home'
        (self.base / 'state').mkdir(parents=True)
        self.path = config_path(self.home)
        self.path.parent.mkdir(parents=True)
        self.checkpoint = self.base / 'state/last-known-good.json'
        self.old = default_config()
        self.old['schema_version'] = 1
        self.old.pop('wake'); self.old.pop('fingerprint')
        self.old['rotation']['direction'] = 'reversed'
        self.old_bytes = json.dumps(self.old, indent=2).encode()
        self.helpers = {name: {'present': True, 'enabled': True, 'active': True}
                        for name in (migration.WAKE_HELPER, migration.FINGERPRINT_HELPER)}

    def test_v03_preferences_adopt_helpers_without_losing_other_choices(self):
        self.path.write_bytes(self.old_bytes)
        self.checkpoint.write_bytes(self.old_bytes)
        self.assertEqual(migration.prepare(self.base, self.home, self.helpers), 'schema-2')
        config = load_config(self.path)
        self.assertEqual(config['rotation']['direction'], 'reversed')
        self.assertEqual(config['wake'], {'slider_open': True, 'keyboard': True})
        self.assertFalse(config['fingerprint']['wake_screen'])
        save_config(self.checkpoint, config)  # Real watcher's first reconciliation.
        migration.rollback(self.base, self.home)
        self.assertEqual(self.path.read_bytes(), self.old_bytes)
        self.assertEqual(self.checkpoint.read_bytes(), self.old_bytes)

    def test_no_helpers_preserves_existing_wake_behavior(self):
        self.path.write_bytes(self.old_bytes)
        migration.prepare(self.base, self.home, {})
        config = load_config(self.path)
        self.assertFalse(config['wake']['slider_open'])
        self.assertFalse(config['wake']['keyboard'])
        self.assertTrue(config['fingerprint']['wake_screen'])

    def test_new_preferences_take_precedence_over_legacy_helpers(self):
        config = default_config()
        save_config(self.path, config)
        before = self.path.read_bytes()
        migration.prepare(self.base, self.home, self.helpers)
        self.assertEqual(self.path.read_bytes(), before)
        migration.commit(self.base)
        self.assertFalse((self.base / 'state' / migration.JOURNAL).exists())

    def test_rollback_retains_concurrent_user_change(self):
        self.path.write_bytes(self.old_bytes)
        migration.prepare(self.base, self.home, self.helpers)
        config = load_config(self.path)
        config['backlight']['enabled'] = False
        save_config(self.path, config)
        with self.assertRaisesRegex(RuntimeError, 'changed after upgrade'):
            migration.rollback(self.base, self.home)
        self.assertFalse(load_config(self.path)['backlight']['enabled'])
        self.assertTrue((self.base / 'state' / migration.JOURNAL).exists())

    def test_new_file_and_checkpoint_are_removed_on_rollback(self):
        migration.prepare(self.base, self.home, self.helpers)
        save_config(self.checkpoint, load_config(self.path))
        migration.rollback(self.base, self.home)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.checkpoint.exists())

    def test_unknown_schema_and_new_flag_types_are_rejected(self):
        for change in [('wake', {'slider_open': 1, 'keyboard': False}),
                       ('fingerprint', {'wake_screen': 'no'}), ('schema_version', 99)]:
            config = default_config()
            config[change[0]] = change[1]
            with self.assertRaises(ValueError):
                validate_config(config)


if __name__ == '__main__':
    unittest.main()
