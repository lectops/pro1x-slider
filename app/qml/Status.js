// SPDX-License-Identifier: GPL-3.0-only
// Pure display logic: an old or unconfirmed report never implies application.
function heading(snapshot, saving, brightnessPending) {
    if (saving || brightnessPending) return "Saving settings…"
    if (snapshot.error) return "Settings need attention"
    if (snapshot.backend === "checking") return "Checking watcher…"
    if (snapshot.backend === "missing") return "Watcher not detected"
    if (snapshot.backend === "stale") return "Watcher stopped reporting"
    if (snapshot.backend === "incompatible") return "Watcher status unavailable"
    var status = snapshot.status
    if (!status) return "Checking watcher…"
    if (status.observed_revision !== snapshot.config.revision) return "Pending · waiting for watcher"
    if (status.state === "recovery_needed") return "Recovery needs attention"
    if (status.state === "degraded") return "Some settings need attention"
    if (snapshot.config.enabled && (
            (snapshot.config.backlight.enabled && !status.capabilities.backlight) ||
            (snapshot.config.rotation.enabled && !status.capabilities.rotation) ||
            (snapshot.config.keyboard.hide_when_open && !status.capabilities.keyboard) ||
            (snapshot.config.wake.slider_open && !status.capabilities.slider_wake) ||
            (snapshot.config.wake.keyboard && !status.capabilities.keyboard_wake) ||
            (!snapshot.config.fingerprint.wake_screen && !status.capabilities.fingerprint)))
        return "Some settings are unavailable"
    if (status.applied_revision !== snapshot.config.revision) return "Pending · applying settings"
    return snapshot.config.enabled ? "Settings applied" : "Paused · overrides restored"
}

function detail(snapshot) {
    if (snapshot.backend === "missing") return "Preferences stay on this phone. Install the companion watcher to apply them."
    if (snapshot.backend === "stale") return "The last report is no longer fresh. Settings are not confirmed as applied."
    if (snapshot.backend === "checking") return "Waiting for a fresh heartbeat before trusting the last report."
    if (snapshot.backend === "incompatible") return "The companion must provide a compatible status report."
    var status = snapshot.status
    if (!status) return ""
    var text = "Slider: " + status.slider + "."
    if (status.config_error) text += " " + status.config_error
    if (status.platform.reload_required === true) text += " The shell must reload before rotation can work."
    if (status.platform.overlay === "missing") text += " Rotation integration is missing."
    if (status.platform.overlay === "unsupported") text += " This shell version does not support rotation integration."
    return text
}

function feature(snapshot, name) {
    if (snapshot.backend !== "running" || !snapshot.status) return "Awaiting a fresh watcher report"
    var feature = snapshot.status.features[name]
    if (name === "fingerprint" && feature.state === "disabled") return "System fingerprint wake behavior"
    if (name === "fingerprint" && feature.state === "active") return "Fingerprint wake blocked"
    var names = {active: "Active", idle: "Idle", disabled: "Disabled", waiting: "Waiting", error: "Needs attention", unsupported: "Unsupported"}
    var text = names[feature.state] || "Unknown"
    if (!snapshot.status.capabilities[name] && feature.state !== "unsupported") text += " · unavailable on this system"
    return text + (feature.message ? " · " + feature.message : "")
}

function canDim(snapshot) {
    return snapshot.backend === "running" && !!snapshot.status && snapshot.status.capabilities.backlight_dimming === true
}

function dimmingDetail(snapshot) {
    if (snapshot.backend !== "running" || !snapshot.status)
        return "Dimming support is not confirmed by a fresh watcher report. Use Light when open for on/off control."
    if (!snapshot.dimming_known)
        return "The watcher has not reported dimming support. Use Light when open for on/off control."
    return "This keyboard light supports on/off only. Use Light when open to control it."
}
