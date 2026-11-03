# SPDX-License-Identifier: GPL-3.0-only
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from build_installer import build, FILES


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parent
        self.artifact = build(self.source, self.root / 'dist')

    def run_installer(self, *arguments):
        return subprocess.run([sys.executable, str(self.artifact), *map(str, arguments)],
                              capture_output=True, text=True, timeout=15)

    def test_complete_source_extracts_without_installing(self):
        destination = self.root / 'extracted'
        result = self.run_installer('--extract', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in FILES:
            self.assertEqual((destination / name).read_bytes(), (self.source / name).read_bytes(), name)

    def test_one_file_help_does_not_request_sudo(self):
        result = self.run_installer('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)

    def test_extract_refuses_to_overwrite_existing_files(self):
        destination = self.root / 'existing'
        destination.mkdir()
        marker = destination / 'keep.txt'
        marker.write_text('keep this')
        result = self.run_installer('--extract', destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'keep this')

    def test_build_is_repeatable_and_checksum_matches(self):
        second = build(self.source, self.root / 'second')
        self.assertEqual(self.artifact.read_bytes(), second.read_bytes())
        digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.assertEqual((self.artifact.parent / 'SHA256SUMS').read_text(),
                         digest + '  ' + self.artifact.name + '\n')


if __name__ == '__main__':
    unittest.main()
