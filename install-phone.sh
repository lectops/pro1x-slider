#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
set -eu
slider_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sudo /usr/bin/python3 "$slider_dir/install.py" install
