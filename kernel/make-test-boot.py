#!/usr/bin/env python3
"""Repack this exact Pro1 X boot image; never flash or contact a device."""
import argparse
import gzip
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

STOCK_SHA256 = "31ca37e71670dbc6d27340fc0a27960738e4a1e294b9f3530f48309fe4ee0ca3"
TOOLS = {
    "avbtool.py": "e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b",
    "mkbootimg-v2.py": "5579fb6bcb9e89e790a70fb9ccf3c00cf56e3e46aef74d84fcb0964887e5576e",
    "unpack_bootimg.py": "a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308",
}
PARTITION_SIZE = 100663296
SALT = "6f8694df2088ff1d46524cf7352b6f2c26ed5d86ba901b7db7c1efb2b9dc7e7e"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_tool(workspace, name, *args):
    path = workspace / name
    if digest(path) != TOOLS[name]:
        raise ValueError(f"Unexpected tool bytes: {name}")
    result = subprocess.run(
        [sys.executable, str(path), *map(str, args)],
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"{name}: {result.stderr.decode()}{result.stdout.decode()}")
    return result.stdout


def unpack(workspace, image, destination):
    result = checked_tool(
        workspace, "unpack_bootimg.py", "--boot_img", image,
        "--out", destination, "--format", "mkbootimg", "--null",
    )
    return [part.decode() for part in result.rstrip(b"\0").split(b"\0")]


def metadata(header):
    """Only kernel length and the legacy component hash may change."""
    header = bytearray(header)
    header[8:12] = bytes(4)
    header[576:608] = bytes(32)
    return header


def build(workspace, kernel, output):
    stock = workspace / "stock-boot.img"
    if output.exists():
        raise ValueError(f"Refusing to replace {output}")
    if digest(stock) != STOCK_SHA256:
        raise ValueError("Stock boot image does not match the recorded build")
    original = stock.read_bytes()
    if len(original) != PARTITION_SIZE or original[:8] != b"ANDROID!":
        raise ValueError("Unexpected stock boot format")
    page_size, header_version = struct.unpack_from("<II", original, 36)
    if (page_size, header_version) != (4096, 2):
        raise ValueError("Only the recorded v2 boot header is supported")
    image = gzip.decompress(kernel.read_bytes())
    if image[56:60] != b"ARM\x64":
        raise ValueError("Kernel must be a gzip-compressed ARM64 Linux Image")

    with tempfile.TemporaryDirectory(prefix=".pro1x-boot-", dir=output.parent) as td:
        temporary = Path(td)
        before, after = temporary / "before", temporary / "after"
        args = unpack(workspace, stock, before)
        args[args.index("--kernel") + 1] = str(kernel)
        candidate = temporary / "boot.img"
        checked_tool(workspace, "mkbootimg-v2.py", *args, "--output", candidate)
        checked_tool(
            workspace, "avbtool.py", "add_hash_footer", "--image", candidate,
            "--partition_name", "boot", "--partition_size", PARTITION_SIZE,
            "--algorithm", "NONE", "--hash_algorithm", "sha256", "--salt", SALT,
            "--internal_release_string", "avbtool 1.2.0",
        )
        verified = checked_tool(workspace, "avbtool.py", "verify_image", "--image", candidate)
        unpack(workspace, candidate, after)
        for component in ("ramdisk", "dtb"):
            if digest(before / component) != digest(after / component):
                raise ValueError(f"Repacking changed {component}")
        if digest(kernel) != digest(after / "kernel"):
            raise ValueError("Repacking changed kernel bytes")
        result = candidate.read_bytes()
        if len(result) != PARTITION_SIZE:
            raise ValueError("Unexpected resulting partition size")
        if metadata(original[:page_size]) != metadata(result[:page_size]):
            raise ValueError("Repacking changed boot metadata")
        # Hard-link into place so even a concurrent writer cannot be overwritten.
        os.link(candidate, output)
        print(verified.decode().strip())
        print(f"SHA256 {digest(output)}  {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    build(options.workspace.resolve(), options.kernel.resolve(), options.output.resolve())
