#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Check the Secure Boot variable seed region of an Apple U-Boot image.

U-Boot built with CONFIG_EFI_VARIABLES_PRESEED and board/apple/mac/uefi-seed.var
(or a seed from tools/apple_secboot_seed.py) carries a fixed-size region that
aurora-secboot fills with the owner's PK, KEK, db and dbx before it signs
Stage 2. It finds the region by the marker at its end, so the image must
hold that marker exactly once, after a valid efi_var_file header.
"""
import struct
import sys
import zlib

MAGIC = 0x0161566966456255
MARKER = b"AURORA-UEFI-SEED"


def regions(image):
    """(offset, size) of each seed region in image."""
    out, at = [], image.find(MARKER)
    while at >= 0:
        size, version = struct.unpack_from("<II", image, at + len(MARKER))
        out.append((at + 32 - size, size, version))
        at = image.find(MARKER, at + 1)
    return out


def check(image):
    found = regions(image)
    if len(found) != 1:
        raise ValueError(f"{len(found)} seed regions, expected exactly one")
    start, size, version = found[0]
    if version != 1 or size < 4096 or start < 0:
        raise ValueError(f"bad seed region: size {size}, version {version}")
    reserved, magic, length, crc = struct.unpack_from("<QQII", image, start)
    if reserved or magic != MAGIC or length < 24 or length > size - 32:
        raise ValueError("the seed region does not start with an efi_var_file header")
    if zlib.crc32(image[start + 24:start + length]) != crc:
        raise ValueError("the seed region's CRC does not match")
    return start, size, length


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} u-boot-nodtb.bin")
    with open(sys.argv[1], "rb") as f:
        image = f.read()
    try:
        start, size, length = check(image)
    except ValueError as e:
        sys.exit(f"{sys.argv[1]}: {e}")
    print(f"{sys.argv[1]}: seed region at {start:#x}, {size} bytes, {length - 24} bytes of variables")


if __name__ == "__main__":
    main()
