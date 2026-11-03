// SPDX-License-Identifier: GPL-3.0-only
import QtQuick 2.12
import Lomiri.Components 1.3

Item {
    id: setting
    property string title
    property string description
    property bool checked: false
    signal toggled(bool value)
    signal focused()
    implicitHeight: Math.max(labels.height, toggle.height) + units.gu(2)
    onCheckedChanged: toggle.checked = checked

    Column {
        id: labels
        anchors { left: parent.left; right: toggle.left; rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
        spacing: units.gu(0.5)
        Label { width: parent.width; text: setting.title; wrapMode: Text.WordWrap; font.bold: true }
        Label { width: parent.width; text: setting.description; wrapMode: Text.WordWrap; textSize: Label.Small; opacity: 0.8 }
    }
    Switch {
        id: toggle
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        activeFocusOnTab: true
        checked: setting.checked
        Accessible.name: setting.title
        onClicked: {
            // Use the confirmed value; the native Switch also toggles itself.
            setting.toggled(!setting.checked)
            Qt.callLater(function() { toggle.checked = setting.checked })
        }
        onActiveFocusChanged: if (activeFocus) setting.focused()
    }
}
