// SPDX-License-Identifier: GPL-3.0-only
import QtQuick 2.12
import Lomiri.Components 1.3
import Lomiri.Components.Popups 1.3
import io.thp.pyotherside 1.5
import "Status.js" as Status

MainView {
    id: root
    objectName: "sliderSettings"
    applicationName: "pro1x-slider.lectops"
    width: units.gu(45)
    height: units.gu(75)
    property bool probeMode: Qt.application.arguments.indexOf("--probe") !== -1
    property bool ready: false
    property bool saving: false
    property bool updating: false
    property bool refreshInFlight: false
    property bool resumedRefreshPending: false
    property string bridgeError: ""
    property string saveError: ""
    property var model: ({config: null, status: null, backend: "checking", error: null})
    property var config: model.config
    property bool controlsEnabled: ready && config !== null && model.backend !== "incompatible" && !saving
    property bool dimmingAvailable: Status.canDim(model)
    onDimmingAvailableChanged: if (!dimmingAvailable) brightnessDelay.stop()

    function receive(result) {
        updating = true
        if (resumedRefreshPending && result.backend === "running") result.backend = "checking"
        if (saveError && !result.error) result.error = saveError
        model = result
        brightness.value = config.backlight.brightness_percent
        updating = false
        bridgeError = ""
    }
    function refresh(resumed) {
        if (resumed) {
            resumedRefreshPending = true
            if (model.backend === "running") model = {config: config, status: model.status, backend: "checking", error: model.error}
        }
        if (!ready || saving || refreshInFlight || brightnessDelay.running || brightness.pressed) return
        refreshInFlight = true
        var method = resumedRefreshPending ? "gui_bridge.resume" : "gui_bridge.snapshot"
        resumedRefreshPending = false
        python.call(method, [], function(result) {
            refreshInFlight = false
            if (!root.saving && !brightnessDelay.running && !brightness.pressed) receive(result)
        })
    }
    function save(patch) {
        if (!controlsEnabled) return
        saveError = ""
        if (brightnessDelay.running) {
            brightnessDelay.stop()
            if (!patch.backlight) patch.backlight = {}
            patch.backlight.brightness_percent = Math.round(brightness.value)
        }
        saving = true
        python.call("gui_bridge.save_patch", [patch], function(result) {
            saving = false
            if (result.error) root.saveError = result.error
            receive(result)
        })
    }
    function defaults() {
        if (!controlsEnabled) return
        saveError = ""
        brightnessDelay.stop()
        saving = true
        python.call("gui_bridge.reset_defaults", [], function(result) {
            saving = false
            if (result.error) root.saveError = result.error
            receive(result)
        })
    }
    function reveal(item) {
        var point = item.mapToItem(scroller.contentItem, 0, 0)
        if (point.y < scroller.contentY) scroller.contentY = Math.max(0, point.y - units.gu(1))
        else if (point.y + item.height > scroller.contentY + scroller.height)
            scroller.contentY = Math.min(Math.max(0, scroller.contentHeight - scroller.height), point.y + item.height - scroller.height + units.gu(1))
    }

    Connections {
        target: Qt.application
        onStateChanged: if (Qt.application.state === Qt.ApplicationActive) root.refresh(true)
    }
    Timer { interval: 2000; repeat: true; running: root.ready && !root.probeMode && Qt.application.state === Qt.ApplicationActive; onTriggered: root.refresh(false) }
    Timer {
        id: brightnessDelay
        interval: 300
        onTriggered: {
            if (brightness.pressed) restart()
            else root.save({backlight: {brightness_percent: Math.round(brightness.value)}})
        }
    }

    Python {
        id: python
        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl("../python"))
            importModule("gui_bridge", function(success) {
                if (root.probeMode && success) {
                    call("gui_bridge.probe", [], function(result) {
                        console.log("SLIDER_SETTINGS_PROBE " + JSON.stringify(result))
                        Qt.quit()
                    })
                } else if (success) {
                    root.ready = true
                    root.refresh(false)
                } else {
                    root.bridgeError = "Could not load the settings bridge."
                    if (root.probeMode) Qt.quit()
                }
            })
        }
        onError: {
            console.error("Slider Settings: " + traceback)
            if (root.saving) {
                root.saveError = "Could not complete the settings request. " + traceback
                root.model = {config: root.config, status: root.model.status, backend: root.model.backend, error: root.saveError}
            }
            root.saving = false
            root.refreshInFlight = false
            root.bridgeError = "Could not complete the settings request. " + traceback
            if (root.probeMode) Qt.quit()
        }
    }

    Page {
        id: page
        anchors.fill: parent
        header: PageHeader {
            title: "Slider Settings"
            trailingActionBar.actions: [Action { text: "About"; iconName: "info"; onTriggered: PopupUtils.open(aboutDialog) }]
        }
        Flickable {
            id: scroller
            anchors.fill: parent
            anchors.topMargin: page.header.height
            contentWidth: width
            contentHeight: content.height + units.gu(4)
            clip: true
            flickableDirection: Flickable.VerticalFlick
            Column {
                id: content
                width: Math.min(scroller.width - units.gu(4), units.gu(114))
                anchors.horizontalCenter: parent.horizontalCenter
                y: units.gu(2)
                spacing: units.gu(2)
                Rectangle {
                    width: parent.width
                    height: statusContent.height + units.gu(4)
                    radius: units.gu(1)
                    color: "#eaf3ef"
                    Column {
                        id: statusContent
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: units.gu(2) }
                        spacing: units.gu(1)
                        Label { width: parent.width; text: Status.heading(root.model, root.saving, brightnessDelay.running); font.bold: true; textSize: Label.Large; color: "#174b52"; wrapMode: Text.WordWrap }
                        Label { width: parent.width; text: Status.detail(root.model); color: "#234747"; wrapMode: Text.WordWrap }
                        Label { width: parent.width; visible: text !== ""; text: root.bridgeError || root.model.error || ""; color: "#9a2d20"; wrapMode: Text.WordWrap }
                    }
                }
                SettingSwitch {
                    width: parent.width
                    title: "Slider automation"
                    description: "Pause to restore preferences and turn the managed keyboard light off. Your feature choices are kept."
                    checked: root.config ? root.config.enabled : false
                    enabled: root.controlsEnabled
                    onToggled: root.save({enabled: value})
                    onFocused: root.reveal(this)
                }
                Rectangle { width: parent.width; height: units.dp(1); color: "#c9d2ce" }
                Flow {
                    id: panels
                    width: parent.width
                    spacing: units.gu(3)
                    property real panelWidth: width >= units.gu(68) ? (width - spacing) / 2 : width
                    Column {
                        width: panels.panelWidth
                        spacing: units.gu(1)
                        Label { text: "KEYBOARD LIGHT"; textSize: Label.Small; font.bold: true }
                        SettingSwitch {
                            width: parent.width
                            title: "Light when open"
                            description: Status.feature(root.model, "backlight")
                            checked: root.config ? root.config.backlight.enabled : false
                            enabled: root.controlsEnabled
                            onToggled: root.save({backlight: {enabled: value}})
                            onFocused: root.reveal(this)
                        }
                        Label { visible: root.dimmingAvailable; text: "Brightness · " + Math.round(brightness.value) + "%" }
                        Slider {
                            id: brightness
                            objectName: "brightnessSlider"
                            visible: root.dimmingAvailable
                            width: parent.width
                            minimumValue: 0
                            maximumValue: 100
                            stepSize: 1
                            live: true
                            enabled: root.controlsEnabled && root.dimmingAvailable
                            activeFocusOnTab: true
                            Accessible.name: "Keyboard light brightness"
                            function formatValue(value) { return Math.round(value) + "%" }
                            onValueChanged: if (root.dimmingAvailable && root.ready && !root.updating && root.config && Math.round(value) !== root.config.backlight.brightness_percent) brightnessDelay.restart()
                            onPressedChanged: if (root.dimmingAvailable && !pressed && root.ready && !root.updating && root.config && Math.round(value) !== root.config.backlight.brightness_percent) brightnessDelay.restart()
                            onActiveFocusChanged: if (activeFocus) root.reveal(this)
                        }
                        Label { visible: root.dimmingAvailable; width: parent.width; text: "0% keeps the light off. Changes also apply while the slider is open."; textSize: Label.Small; opacity: 0.8; wrapMode: Text.WordWrap }
                        Label { visible: !root.dimmingAvailable; width: parent.width; text: Status.dimmingDetail(root.model); textSize: Label.Small; opacity: 0.8; wrapMode: Text.WordWrap }
                    }
                    Column {
                        width: panels.panelWidth
                        spacing: units.gu(1)
                        Label { text: "DISPLAY & KEYBOARD"; textSize: Label.Small; font.bold: true }
                        SettingSwitch {
                            width: parent.width
                            title: "Landscape when open"
                            description: Status.feature(root.model, "rotation")
                            checked: root.config ? root.config.rotation.enabled : false
                            enabled: root.controlsEnabled
                            onToggled: root.save({rotation: {enabled: value}})
                            onFocused: root.reveal(this)
                        }
                        Label { text: "Landscape direction" }
                        Row {
                            width: parent.width
                            spacing: units.gu(1)
                            Button {
                                width: (parent.width - parent.spacing) / 2
                                text: "Keyboard"
                                color: root.config && root.config.rotation.direction === "keyboard" ? "#4daca7" : theme.palette.normal.base
                                enabled: root.controlsEnabled
                                activeFocusOnTab: true
                                Accessible.name: "Landscape toward the keyboard"
                                onClicked: root.save({rotation: {direction: "keyboard"}})
                                onActiveFocusChanged: if (activeFocus) root.reveal(this)
                            }
                            Button {
                                width: (parent.width - parent.spacing) / 2
                                text: "Reversed"
                                color: root.config && root.config.rotation.direction === "reversed" ? "#4daca7" : theme.palette.normal.base
                                enabled: root.controlsEnabled
                                activeFocusOnTab: true
                                Accessible.name: "Reversed landscape direction"
                                onClicked: root.save({rotation: {direction: "reversed"}})
                                onActiveFocusChanged: if (activeFocus) root.reveal(this)
                            }
                        }
                        SettingSwitch {
                            width: parent.width
                            title: "Hide on-screen keyboard"
                            description: Status.feature(root.model, "keyboard")
                            checked: root.config ? root.config.keyboard.hide_when_open : false
                            enabled: root.controlsEnabled
                            onToggled: root.save({keyboard: {hide_when_open: value}})
                            onFocused: root.reveal(this)
                        }
                    }
                }
                Rectangle { width: parent.width; height: units.dp(1); color: "#c9d2ce" }
                Label { width: parent.width; text: "Each feature works independently. Closing the slider restores preferences owned by the watcher."; textSize: Label.Small; opacity: 0.8; wrapMode: Text.WordWrap }
                Button {
                    text: "Restore defaults"
                    enabled: root.controlsEnabled
                    activeFocusOnTab: true
                    onClicked: PopupUtils.open(resetDialog)
                    onActiveFocusChanged: if (activeFocus) root.reveal(this)
                }
            }
        }
    }
    Component {
        id: resetDialog
        Dialog {
            id: resetPopup
            title: "Restore slider defaults?"
            text: root.dimmingAvailable ? "Enable all features, set brightness to 100%, and use the keyboard landscape direction." : "Enable all features and use the keyboard landscape direction."
            Button { text: "Restore defaults"; color: "#4daca7"; onClicked: { PopupUtils.close(resetPopup); root.defaults() } }
            Button { text: "Cancel"; onClicked: PopupUtils.close(resetPopup) }
        }
    }
    Component {
        id: aboutDialog
        Dialog {
            id: aboutPopup
            title: "Slider Settings"
            text: "Version 0.3.0\nFor the F(x)tec Pro1-X on Ubuntu Touch.\n\nPreferences are stored locally. The companion watcher applies them without restarting the service.\n\nGPL-3.0-only"
            Button { text: "Close"; onClicked: PopupUtils.close(aboutPopup) }
        }
    }
}
