#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Build a U-Boot variable seed (CONFIG_EFI_VAR_SEED_FILE) holding PK, KEK, db
and dbx from EFI signature lists (cert-to-efi-sig-list output).

  apple_secboot_seed.py --pk PK.esl --kek KEK.esl --db db.esl [--dbx dbx.esl] -o ubootefi.var

With CONFIG_EFI_VARIABLES_PRESEED these variables can only come from the seed:
U-Boot ignores secure boot variables (and shim's MOK variables) in a variable
file on the ESP, and refuses writes to them.
"""
import argparse
import struct
import uuid
import zlib

MAGIC = 0x0161566966456255
GLOBAL = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
IMAGE_DB = uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f")
CERT_SHA256 = uuid.UUID("c1c41626-504c-4092-aca9-41f936934328")
NV_BS_RT_AT = 0x1 | 0x2 | 0x4 | 0x20


def entry(guid, name, data, attr=NV_BS_RT_AT):
    nd = name.encode("utf-16-le") + b"\0\0" + data
    nd += b"\0" * (-len(nd) % 8)
    return struct.pack("<IIQ16s", len(data), attr, 0, guid.bytes_le) + nd


def empty_dbx():
    # One all-zero SHA-256 entry, so dbx exists and matches nothing real.
    owner = uuid.UUID("00000000-0000-0000-0000-000000000000").bytes_le
    sig = owner + b"\0" * 32
    return struct.pack("<16sIII", CERT_SHA256.bytes_le, 28 + len(sig), 0, len(sig)) + sig


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pk", required=True)
    ap.add_argument("--kek", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--dbx")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    ents = entry(GLOBAL, "PK", open(a.pk, "rb").read())
    ents += entry(GLOBAL, "KEK", open(a.kek, "rb").read())
    ents += entry(IMAGE_DB, "db", open(a.db, "rb").read())
    ents += entry(IMAGE_DB, "dbx", open(a.dbx, "rb").read() if a.dbx else empty_dbx())
    hdr = struct.pack("<QQII", 0, MAGIC, 24 + len(ents), zlib.crc32(ents) & 0xFFFFFFFF)
    open(a.output, "wb").write(hdr + ents)


if __name__ == "__main__":
    main()
