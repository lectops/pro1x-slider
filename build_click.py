#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage the architecture-independent native Click source for click/Clickable."""
import argparse
from pathlib import Path
import shutil


def stage_app(source_root, destination):
    """Copy only app resources and the shared protocol module into an empty dir."""
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Click staging destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root / "app", destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    shutil.copy2(source_root / "slider_common.py", destination / "python" / "slider_common.py")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(source_root / name, destination / name)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    options = parser.parse_args()
    print(stage_app(options.source, options.destination))


if __name__ == "__main__":
    main()
