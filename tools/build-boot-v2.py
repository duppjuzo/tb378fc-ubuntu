#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a safe Android header-v2 temporary RAM boot image (`fastboot boot`).

Always test new kernels via `fastboot boot boot-v2.img` before generating or
flashing a persistent GKI v4 boot triplet.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'third_party/oneplus/tools'))
from lib.abl_layout import sink_layout

DEFAULT_CMDLINE = "rdinit=/init panic=15 mem_sleep_default=s2idle loglevel=4"
DEBUG_CMDLINE = "tb378fc.debug=1 log_buf_len=2M printk.time=1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kernel", type=Path, required=True, help="Compiled arm64 Image")
    p.add_argument("--dtb", type=Path, required=True, help="Compiled sm8750-lenovo-tb378fc.dtb")
    p.add_argument("--initramfs", type=Path, required=True, help="Initramfs .cpio.gz")
    p.add_argument("--mkbootimg", type=Path, required=True, help="Path to AOSP mkbootimg.py")
    p.add_argument("--output", type=Path, required=True, help="Output directory (created if missing)")
    p.add_argument("--root", default="", help="Root specifier for tb378fc.root= (e.g. PARTLABEL=linux_rootfs or UUID=...)")
    p.add_argument("--debug", action="store_true", help="Enable opt-in tb378fc.debug=1 USB gadget + httpd:8081")
    p.add_argument("--cmdline", default=DEFAULT_CMDLINE, help="Base kernel command line")
    p.add_argument("--cmdline-extra", default="", help="Extra kernel parameters appended to cmdline")
    p.add_argument("--os-version", default="15.0.0")
    p.add_argument("--os-patch-level", default="2026-07")
    a = p.parse_args()

    for name in ("kernel", "dtb", "initramfs", "mkbootimg"):
        path = getattr(a, name)
        if not path.is_file():
            raise SystemExit(f"Missing required input --{name}: {path}")

    parts = [a.cmdline.strip()]
    if a.root.strip():
        parts.append(f"tb378fc.root={a.root.strip()}")
    if a.debug:
        parts.append(DEBUG_CMDLINE)
    if a.cmdline_extra.strip():
        parts.append(a.cmdline_extra.strip())
    cmdline = " ".join(part for part in parts if part)

    a.output.mkdir(parents=True, exist_ok=True)
    boot_img = a.output / "boot-v2.img"

    cmd = [
        sys.executable,
        str(a.mkbootimg.resolve()),
        "--header_version", "2",
        "--pagesize", "4096",
        "--base", "0x00000000",
        "--kernel_offset", "0x00008000",
        "--ramdisk_offset", "0x01000000",
        "--tags_offset", "0x00000100",
        "--dtb_offset", "0x01f00000",
        "--os_version", a.os_version,
        "--os_patch_level", a.os_patch_level,
        "--kernel", str(a.kernel.resolve()),
        "--ramdisk", str(a.initramfs.resolve()),
        "--dtb", str(a.dtb.resolve()),
        "--cmdline", cmdline,
        "--output", str(boot_img.resolve()),
    ]
    subprocess.run(cmd, check=True)

    manifest = {
        "layout": "Android-boot-header-v2-fastboot-ram",
        "device": "Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)",
        "abl_sink_layout": sink_layout(a.dtb.read_bytes()),
        "cmdline": cmdline,
        "debug_enabled": a.debug,
        "os_version": a.os_version,
        "os_patch_level": a.os_patch_level,
        "inputs": {
            "kernel": {"path": str(a.kernel), "sha256": sha256(a.kernel), "bytes": a.kernel.stat().st_size},
            "dtb": {"path": str(a.dtb), "sha256": sha256(a.dtb), "bytes": a.dtb.stat().st_size},
            "initramfs": {"path": str(a.initramfs), "sha256": sha256(a.initramfs), "bytes": a.initramfs.stat().st_size},
        },
        "output": {
            "boot_v2_img": {
                "path": str(boot_img),
                "sha256": sha256(boot_img),
                "bytes": boot_img.stat().st_size,
            }
        },
        "fastboot_command": f"fastboot boot {boot_img.name}",
    }
    (a.output / "boot-v2.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Built temporary RAM boot image: {boot_img} ({boot_img.stat().st_size} bytes)")
    layout = manifest["abl_sink_layout"]
    if layout == "none":
        print("NOTE: DTB has no ABL overlay sink (layout=none). Before fastboot boot on a stock-DTBO tablet, "
              "wrap the DTB with your stock vendor_boot/dtbo via third_party/oneplus/tools/build-erhai-abl-symbols.py.")
    print("WARNING: Lenovo TB378FC stock ABL does not support fastboot boot (Header v2). "
          "Prefer tools/build-boot-triplet.py (Header v4) for physical TB378FC testing.")


if __name__ == "__main__":
    main()
