# SPDX-License-Identifier: GPL-3.0-only
"""Exact-build Lomiri override; the original image remains read-only."""
import hashlib

STOCK_SHA256 = '633578d225f0dd41a656b27c08e0d5cc0901e6b6267c74c26edab8823524dec8'
TARGET = '/usr/share/lomiri/OrientedShell.qml'
MARKER = '// Pro1 X slider override (2026-11-03)'

SETTINGS = '''    // Pro1 X slider override (2026-11-03)
    // Keep the ordinary orientation tracking and rotation-lock preference intact.
    GSettings {
        id: pro1xRotationSettings
        schema.id: "com.lomiri.touch.system"
    }
    readonly property int pro1xForcedOrientation: {
        switch (pro1xRotationSettings.orientationLock) {
        case "LandscapeOrientation": return Qt.LandscapeOrientation;
        case "InvertedLandscapeOrientation": return Qt.InvertedLandscapeOrientation;
        case "PortraitOrientation": return Qt.PortraitOrientation;
        case "InvertedPortraitOrientation": return Qt.InvertedPortraitOrientation;
        case "PrimaryOrientation": return orientations.primary;
        default: return 0;
        }
    }

'''


def patched(stock):
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        raise RuntimeError('Lomiri QML differs from the tested build; refusing to patch it')
    text = stock.decode()
    changes = [
        ('    property int physicalOrientation:', SETTINGS + '    property int physicalOrientation:'),
        ('orientation !== physicalOrientation && !shell.showingGreeter',
         'orientation !== physicalOrientation && !shell.showingGreeter && pro1xForcedOrientation === 0'),
        ('    property bool orientationChangesEnabled:\n',
         '    property bool orientationChangesEnabled:\n        pro1xForcedOrientation !== 0 ||\n'),
        ('    property int acceptedOrientationAngle: {\n',
         '    property int acceptedOrientationAngle: {\n'
         '        if (pro1xForcedOrientation !== 0) {\n'
         '            return QtQuickWindow.Screen.angleBetween(orientations.native_, pro1xForcedOrientation);\n'
         '        }\n'),
    ]
    for old, new in changes:
        if text.count(old) != 1:
            raise RuntimeError('Expected exactly one QML patch anchor: ' + old)
        text = text.replace(old, new, 1)
    return text.encode()
