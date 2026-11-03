# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from build_installer import build, FILES, CLICK_NAME


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parent
        self.click = self.root / CLICK_NAME
        self.click.write_bytes(b'tiny fixture Click payload; never installed')
        self.artifact = build(self.source, self.root / 'dist', self.click)

    def run_installer(self, *arguments, env=None):
        return subprocess.run([sys.executable, str(self.artifact), *map(str, arguments)],
                              capture_output=True, text=True, timeout=15, env=env)

    def test_complete_source_extracts_without_installing(self):
        destination = self.root / 'extracted'
        result = self.run_installer('--extract', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in FILES:
            self.assertEqual((destination / name).read_bytes(), (self.source / name).read_bytes(), name)
        self.assertEqual((destination / CLICK_NAME).read_bytes(), self.click.read_bytes())
        for path in (self.source / 'app').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                self.assertEqual((destination / path.relative_to(self.source)).read_bytes(), path.read_bytes())

    def test_one_file_help_does_not_request_sudo(self):
        result = self.run_installer('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)

    def privilege_mocks(self):
        commands = self.root / 'commands'
        commands.mkdir()
        for name, script in {
            'id': '#!/bin/sh\necho 1000\n',
            'sudo': '#!/bin/sh\nprintf "%s\\n" "$@" > "$MOCK_SUDO_LOG"\n',
        }.items():
            command = commands / name
            command.write_text(script)
            command.chmod(0o755)
        log = self.root / 'sudo.log'
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ.get('PATH', ''),
                   MOCK_SUDO_LOG=str(log))
        return log, env

    def test_combined_install_and_uninstall_route_through_wrapper(self):
        log, env = self.privilege_mocks()
        for arguments, expected in [(('--no-reload',), 'install'),
                                    (('install', '--no-reload'), 'install'),
                                    (('uninstall', '--no-reload'), 'uninstall')]:
            with self.subTest(arguments=arguments):
                result = self.run_installer(*arguments, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                command = log.read_text().splitlines()
                self.assertEqual(command[0], '/usr/bin/python3')
                self.assertEqual(Path(command[1]).name, 'install.py')
                self.assertEqual(command[2:], [expected])

    def test_unknown_update_flag_fails_before_requesting_privilege(self):
        log, env = self.privilege_mocks()
        result = self.run_installer('--update', env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unknown argument', result.stderr)
        self.assertFalse(log.exists())

    def test_extract_refuses_to_overwrite_existing_files(self):
        destination = self.root / 'existing'
        destination.mkdir()
        marker = destination / 'keep.txt'
        marker.write_text('keep this')
        result = self.run_installer('--extract', destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'keep this')

    def test_build_is_repeatable_and_checksum_matches(self):
        second = build(self.source, self.root / 'second', self.click)
        self.assertEqual(self.artifact.read_bytes(), second.read_bytes())
        digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.assertEqual((self.artifact.parent / 'SHA256SUMS').read_text(),
                         digest + '  ' + self.artifact.name + '\n')

    def test_builder_requires_explicit_versioned_click_artifact(self):
        with self.assertRaisesRegex(ValueError, '--click-package'):
            build(self.source, self.root / 'missing')
        wrong = self.root / 'wrong.click'
        wrong.write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError, 'Expected Click package'):
            build(self.source, self.root / 'wrong', wrong)

    def test_extracted_source_can_rebuild_identical_installer(self):
        destination = self.root / 'rebuild'
        result = self.run_installer('--extract', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(destination / 'build_installer.py'),
                                 '--output-dir', str(self.root / 'rebuilt'),
                                 '--click-package', str(destination / CLICK_NAME)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.artifact.read_bytes(), (self.root / 'rebuilt' / self.artifact.name).read_bytes())


if __name__ == '__main__':
    unittest.main()
