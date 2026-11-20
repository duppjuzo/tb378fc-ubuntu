#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate and install Lenovo TB378FC vendor-signed firmware into a rootfs /lib/firmware tree.

Copies owner-extracted stock TB378FC firmware (vpu35_4v.mbn, gen80000_zap.mbn,
adsp.mdt/adsp_dtb.mdt/adsp.b*, novatek/tb378fc.bin, aw88480_acf.bin, WCN7860)
into the target rootfs without distributing proprietary binaries in Git.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

TARGETS = {
    "vpu35_4v.mbn": "qcom/sm8750/tb378fc/vpu35_4v.mbn",
    "gen80000_zap.mbn": "qcom/sm8750/tb378fc/gen80000_zap.mbn",
    "adsp.mdt": "qcom/sm8750/tb378fc/adsp.mdt",
    "adsp_dtb.mdt": "qcom/sm8750/tb378fc/adsp_dtb.mdt",
    "tb378fc.bin": "novatek/tb378fc.bin",
    "novatek_ts_fw.bin": "novatek/tb378fc.bin",
    "aw88480_acf.bin": "aw88480_acf.bin",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory with extracted TB378FC stock firmware")
    parser.add_argument("--rootfs", type=Path, default=Path("/"), help="Target rootfs mountpoint (default: /)")
    args = parser.parse_args()

    src = args.source_dir.resolve()
    fw_root = args.rootfs.resolve() / "lib/firmware"
    installed = []

    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        name = f.name
        rel_dst = None
        if name in TARGETS:
            rel_dst = TARGETS[name]
        elif name.startswith(("adsp.b", "adsp_dtb.b", "adspr.", "adsps.", "adspua.", "adspuo.")):
            rel_dst = f"qcom/sm8750/tb378fc/{name}"
        elif name in ("amss.bin", "m3.bin", "board-2.bin", "regdb.bin", "qdss.cfg", "aux_ucode.bin", "bdwlan.elf"):
            rel_dst = f"ath12k/WCN7860/hw2.0/{name}"
        elif name.startswith(("gngbtfw", "gngbtnv", "brhbtfw", "brhbtnv")):
            rel_dst = f"qca/{name}"
        if rel_dst:
            dst = fw_root / rel_dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            dst.chmod(0o644)
            installed.append((rel_dst, dst.stat().st_size, sha256(dst)[:16]))

    if not installed:
        print(f"No recognized TB378FC firmware files found under {src}", file=sys.stderr)
        return 1

    for rel, sz, digest in installed:
        print(f"  [OK] /lib/firmware/{rel} ({sz} B, sha256={digest}...)")
    print(f"Installed {len(installed)} firmware files into {fw_root}")

    missing = [rel for rel in dict.fromkeys(TARGETS.values()) if not (fw_root / rel).is_file()]
    if missing:
        print("Still missing vendor-signed TB378FC firmware files:")
        for m in missing:
            print(f"  [MISSING] /lib/firmware/{m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
