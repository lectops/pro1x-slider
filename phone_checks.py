#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Validate the real patched QML using a private in-memory settings backend."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from qml_patch import TARGET, SETTINGS, patched

HARNESS_START = '''import QtQuick 2.15
import QtQuick.Window 2.2 as QtQuickWindow
import QtTest 1.2
import GSettings 1.0
TestCase {
    id: root
    name: "SliderRotation"
    property var orientations: ({native_: Qt.PortraitOrientation, primary: Qt.PortraitOrientation})
    property int orientation: Qt.PortraitOrientation
    property int supportedOrientations: 15
    QtObject {
        id: shell
        property int orientation: Qt.PortraitOrientation
        property int orientationAngle: 0
        property int mainAppWindowOrientationAngle: 0
        property bool orientationChangesEnabled: false
    }
'''
HARNESS_TESTS = '''
    function init() {
        pro1xRotationSettings.orientationLock = "none";
        root.orientation = Qt.PortraitOrientation;
        shell.orientationChangesEnabled = false;
    }
    function test_force_and_restore_locked_portrait() {
        compare(pro1xForcedOrientation, 0);
        compare(acceptedOrientationAngle, 0);
        compare(orientationChangesEnabled, false);
        pro1xRotationSettings.orientationLock = "LandscapeOrientation";
        compare(pro1xForcedOrientation, Qt.LandscapeOrientation);
        compare(acceptedOrientationAngle, 270);
        compare(orientationChangesEnabled, true);
        compare(root.orientation, Qt.PortraitOrientation);
        pro1xRotationSettings.orientationLock = "none";
        compare(acceptedOrientationAngle, 0);
        compare(orientationChangesEnabled, false);
    }
    function test_both_landscape_directions() {
        pro1xRotationSettings.orientationLock = "InvertedLandscapeOrientation";
        compare(acceptedOrientationAngle, 90);
    }
    function test_restore_auto_rotation_to_latest_orientation() {
        pro1xRotationSettings.orientationLock = "LandscapeOrientation";
        root.orientation = Qt.InvertedPortraitOrientation;
        compare(acceptedOrientationAngle, 270);
        pro1xRotationSettings.orientationLock = "none";
        compare(acceptedOrientationAngle, 180);
    }
}
'''


def harness(text):
    start = text.index('    property int acceptedOrientationAngle: {')
    accepted = text[start:text.index('    function angleToOrientation', start)]
    start = text.index('    property bool orientationChangesEnabled:')
    enabled = text[start:text.index('    Binding {', start)]
    return HARNESS_START + SETTINGS + accepted + enabled + HARNESS_TESTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qt-prefix', type=Path, default=Path('/'))
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    result = patched(Path(TARGET).read_bytes())
    prefix = args.qt_prefix
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       GSETTINGS_BACKEND='memory', QML_DISABLE_DISK_CACHE='1')
    library = str(prefix / 'usr/lib/aarch64-linux-gnu')
    environment['LD_LIBRARY_PATH'] = library + ':' + os.environ.get('LD_LIBRARY_PATH', '')
    with tempfile.TemporaryDirectory(prefix='pro1x-slider-qa-') as temporary:
        work = Path(temporary)
        (work / 'OrientedShell.qml').write_bytes(result)
        (work / 'tst_rotation.qml').write_text(harness(result.decode()))
        commands = [
            ['python3', '-m', 'unittest', '-v', 'test_slider.py'],
            [str(prefix / 'usr/lib/qt5/bin/qmllint'), str(work / 'OrientedShell.qml')],
            ['systemd-analyze', 'verify', '--man=no', str(source / 'pro1x-slider.service')],
            [str(prefix / 'usr/lib/qt5/bin/qmltestrunner'), '-input', str(work / 'tst_rotation.qml'),
             '-import', '/usr/lib/aarch64-linux-gnu/lomiri/qml',
             '-import', str(prefix / 'usr/lib/aarch64-linux-gnu/qt5/qml')],
        ]
        failed = False
        for command in commands:
            print('RUN', ' '.join(command), flush=True)
            check = subprocess.run(command, cwd=source, env=environment,
                                   text=True, capture_output=True, timeout=30)
            print(check.stdout + check.stderr, flush=True)
            failed |= check.returncode != 0
        raise SystemExit(int(failed))


if __name__ == '__main__':
    main()
