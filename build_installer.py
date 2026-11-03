#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a self-contained installer using only Python's standard library."""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import textwrap
import zipfile

from slider_common import APP_ID, VERSION

CLICK_NAME = APP_ID + '_' + VERSION + '_all.click'
FILES = (
    '.gitignore', 'README.md', 'CHANGELOG.md', 'LICENSE', 'NOTICE',
    'slider.py', 'slider_common.py', 'qml_patch.py', 'install.py',
    'pro1x-slider.service', 'pro1x-slider-platform.service',
    'install-phone.sh', 'phone_checks.py', 'test_slider.py',
    'test_install.py', 'test_package.py', 'test_backend.py', 'test_gui_bridge.py',
    'build_installer.py', 'build_click.py', 'tools/stage_ui_qa.py',
    'docs/VALIDATION.md', 'docs/CONTRACT.md',
)

LOADER = '''#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Pro1-X slider @VERSION@: self-contained source and installer.

Run: python3 pro1x-slider-@VERSION@.py
Inspect source: python3 pro1x-slider-@VERSION@.py --extract DIRECTORY
Remove: python3 pro1x-slider-@VERSION@.py uninstall
"""
import base64
import hashlib
import io
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import zipfile

FILES = @FILES@
SHA256 = '@SHA256@'
PAYLOAD = """@PAYLOAD@"""


def extract(destination):
    data = base64.b64decode(''.join(PAYLOAD.split()), validate=True)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Installer payload checksum mismatch')
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError('Extraction directory must be empty')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(FILES) or set(names) != set(FILES):
            raise RuntimeError('Unexpected files in installer payload')
        destination.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ['--extract']:
        if len(arguments) != 2:
            raise RuntimeError('Usage: --extract DIRECTORY')
        extract(arguments[1])
        print('Source extracted to ' + str(Path(arguments[1]).resolve()))
        return 0
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    with tempfile.TemporaryDirectory(prefix='pro1x-slider-') as temporary:
        extract(temporary)
        return subprocess.call(['/bin/sh', str(Path(temporary) / 'install-phone.sh'), *arguments])


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, zipfile.BadZipFile) as error:
        print('Error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
'''


def build(source, output, click_package=None):
    source, output = Path(source), Path(output)
    if click_package is None:
        raise ValueError('Supply the built Click package with --click-package')
    click_package = Path(click_package)
    if click_package.name != CLICK_NAME:
        raise ValueError('Expected Click package named ' + CLICK_NAME)
    app_files = tuple(sorted(str(path.relative_to(source)) for path in (source / 'app').rglob('*')
                             if path.is_file() and '__pycache__' not in path.parts
                             and path.suffix not in ('.pyc', '.pyo')))
    if not app_files:
        raise ValueError('App source is missing')
    files = (*FILES, *app_files, CLICK_NAME)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in files:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (click_package if name == CLICK_NAME else source / name).read_bytes())
    payload = buffer.getvalue()
    text = LOADER.replace('@VERSION@', VERSION).replace('@FILES@', repr(files))
    text = text.replace('@SHA256@', hashlib.sha256(payload).hexdigest())
    text = text.replace('@PAYLOAD@', '\n'.join(textwrap.wrap(base64.b64encode(payload).decode(), 76)))
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / ('pro1x-slider-' + VERSION + '.py')
    artifact.write_text(text)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(digest + '  ' + artifact.name + '\n')
    return artifact


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'dist')
    parser.add_argument('--click-package', type=Path, required=True,
                        help='Built ' + CLICK_NAME + ' to include alongside its source')
    options = parser.parse_args()
    print(build(Path(__file__).resolve().parent, options.output_dir, options.click_package))
