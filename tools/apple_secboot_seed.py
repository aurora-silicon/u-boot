#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Build a U-Boot variable seed (CONFIG_EFI_VAR_SEED_FILE) holding PK, KEK, db
and dbx from EFI signature lists (cert-to-efi-sig-list output).

  apple_secboot_seed.py --pk PK.esl --kek KEK.esl --db db.esl [--dbx dbx.esl] -o ubootefi.var

With CONFIG_EFI_VARIABLES_PRESEED these variables can only come from the seed:
U-Boot ignores secure boot variables (and shim's MOK variables) in a variable
file on the ESP, and refuses writes to them.

The lists may hold the distribution's keys or keys the machine owner made.
A list that holds one of Microsoft's UEFI CAs is refused: they would admit
every boot loader Microsoft has signed, revoked ones included.
"""
import argparse
import struct
import uuid
import zlib

MAGIC = 0x0161566966456255
GLOBAL = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")
IMAGE_DB = uuid.UUID("d719b2cb-3d3a-4596-a3bc-dad00e67656f")
CERT_SHA256 = uuid.UUID("c1c41626-504c-4092-aca9-41f936934328")
CERT_X509 = uuid.UUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072")
NV_BS_RT_AT = 0x1 | 0x2 | 0x4 | 0x20
# Subject common names of Microsoft's UEFI and Windows CAs.
REFUSED_CAS = (
    b"Microsoft Corporation UEFI CA",
    b"Microsoft UEFI CA",
    b"Microsoft Windows Production PCA",
    b"Windows UEFI CA",
    b"Microsoft Option ROM UEFI CA",
    b"Microsoft Corporation KEK CA",
)


def x509_certs(esl):
    """Yield the X.509 certificates in an EFI signature list file."""
    off = 0
    while off + 28 <= len(esl):
        stype, lsize, hsize, ssize = struct.unpack_from("<16sIII", esl, off)
        if lsize < 28 + hsize or ssize < 16 or off + lsize > len(esl):
            raise SystemExit("malformed EFI signature list")
        if uuid.UUID(bytes_le=stype) == CERT_X509:
            p = off + 28 + hsize
            while p + ssize <= off + lsize:
                yield esl[p + 16:p + ssize]
                p += ssize
        off += lsize
    if off != len(esl):
        raise SystemExit("trailing bytes after the EFI signature lists")


def check_keys(name, esl):
    for cert in x509_certs(esl):
        for ca in REFUSED_CAS:
            if ca in cert:
                raise SystemExit(f"{name}: refusing {ca.decode()}: only the "
                                 "distribution's or the owner's own keys go in the seed")


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
    lists = {n: open(f, "rb").read() for n, f in (("PK", a.pk), ("KEK", a.kek), ("db", a.db))}
    for n, esl in lists.items():
        check_keys(n, esl)
    ents = entry(GLOBAL, "PK", lists["PK"])
    ents += entry(GLOBAL, "KEK", lists["KEK"])
    ents += entry(IMAGE_DB, "db", lists["db"])
    ents += entry(IMAGE_DB, "dbx", open(a.dbx, "rb").read() if a.dbx else empty_dbx())
    hdr = struct.pack("<QQII", 0, MAGIC, 24 + len(ents), zlib.crc32(ents) & 0xFFFFFFFF)
    open(a.output, "wb").write(hdr + ents)


if __name__ == "__main__":
    main()
