#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package an Aurora U-Boot build and its exact source/configuration."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--config", choices=("apple_m1", "apple_j873"), required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--compiler", default="gcc")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    git = lambda *a: subprocess.check_output(["git", *a], cwd=repo, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Refusing to package modified tracked sources")
    build = args.build_dir.resolve()
    files = [build / n for n in ("u-boot-nodtb.bin", "u-boot.bin", "u-boot.dtb", ".config")]
    files += [repo / "doc/board/apple/aurora-releases/0.1.0.md"]
    files += sorted(p for p in (repo / "Licenses").rglob("*") if p.is_file())
    manifest = {
        "project": "aurora-silicon/u-boot", "version": args.version,
        "commit": git("rev-parse", "HEAD"), "config": args.config + "_defconfig",
        "uboot_version": (build / "include/config/uboot.release").read_text().strip(),
        "compiler": subprocess.check_output([args.compiler, "--version"], text=True).splitlines()[0],
        "hardware_tested": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    name = f"u-boot-{args.version}-{args.config}"
    with zipfile.ZipFile(args.output / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        hashes = []
        for path in files:
            relative = path.relative_to(build) if path.is_relative_to(build) else path.relative_to(repo)
            archive.write(path, str(relative))
            hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
        payload = gzip.compress((build / "u-boot-nodtb.bin").read_bytes(), mtime=0)
        archive.writestr("u-boot-nodtb.bin.gz", payload)
        hashes.append(f"{hashlib.sha256(payload).hexdigest()}  u-boot-nodtb.bin.gz")
        archive.writestr("BUILD-INFO.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("SHA256SUMS", "\n".join(hashes) + "\n")
        archive.writestr("README.txt", (
            f"Aurora U-Boot {args.version}, {args.config}; source {manifest['commit']}\n"
            "Use u-boot-nodtb.bin (or its gzip) with Aura and board-matching DTBs.\n"
            "u-boot.bin includes the build's default t8103-j274 control DTB; do not\n"
            "treat it or u-boot.dtb as a Neo, M5, or M6 device tree.\n"
            "apple_j873 is the separate M6 RAM-boot configuration. Automatic boot\n"
            "and persistent EFI variable storage are disabled in that configuration.\n"
            "These exact release artifacts have not been boot-tested on hardware.\n"
        ))
    with zipfile.ZipFile(args.output / f"{name}-debug.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for n in ("u-boot", "u-boot.map", "u-boot.sym"):
            archive.write(build / n, n)
        archive.writestr("BUILD-INFO.json", json.dumps(manifest, indent=2) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
            for p in sorted(args.output.glob("*.zip"))]
    (args.output / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(f"Packaged {name} from {manifest['commit']}")


if __name__ == "__main__":
    main()
