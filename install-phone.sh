#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
set -eu
slider_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
action=install
reload=ask
for argument in "$@"; do
    case "$argument" in
        install|uninstall) action=$argument ;;
        --no-reload) reload=no ;;
        -h|--help)
            echo 'Usage: sh install-phone.sh [install|uninstall] [--no-reload]'
            exit 0 ;;
        *) echo "Unknown argument: $argument" >&2; exit 2 ;;
    esac
done
if [ "$(id -u)" -eq 0 ]; then
    /usr/bin/python3 "$slider_dir/install.py" "$action"
else
    sudo /usr/bin/python3 "$slider_dir/install.py" "$action"
fi
if [ "$reload" = no ]; then
    exit 0
fi
if [ ! -t 0 ]; then
    echo 'Save open apps, then reboot the phone to finish applying the change.'
    exit 0
fi
printf 'Reload Lomiri now? This closes running apps. [y/N] '
if ! read -r answer; then
    answer=n
fi
case "$answer" in
    y|Y|yes|YES)
        if [ "$(id -u)" -eq 0 ]; then
            phablet_uid=$(id -u phablet)
            runuser -u phablet -- env "XDG_RUNTIME_DIR=/run/user/$phablet_uid" \
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$phablet_uid/bus" \
                systemctl --user restart lomiri-full-greeter.service
        else
            systemctl --user restart lomiri-full-greeter.service
        fi ;;
    *) echo 'Reboot the phone later to finish applying the change.' ;;
esac
