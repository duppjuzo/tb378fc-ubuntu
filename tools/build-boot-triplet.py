#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build and AVB-sign Android Header v4 boot images for Lenovo TB378FC.

Lenovo TB378FC stock ABL does not support `fastboot boot` (Header v2).
All testing and daily boots use Android Header v4 partition images:
  - boot.img        (96 MiB): kernel Image + cmdline (no ramdisk)
  - init_boot.img   ( 8 MiB): empty gzip newc ramdisk + AVB footer
  - vendor_boot.img (96 MiB): TB378FC initramfs + ABL-wrapped DTB + AVB footer
  - vbmeta.img      (optional, with --with-vbmeta): AVB --flags 3 chain image

Two signing modes are supported:
  1. --stock <dir>  (recommended for same-slot dual-boot preserving stock vbmeta):
     reads partition sizes and rollback indices from your own stock triplet via
     third_party/oneplus/tools/sign-boot-triplet.py.
  2. Standalone (omit --stock, e.g. inactive-slot testing with --with-vbmeta):
     uses TB378FC verified partition sizes (96 MiB / 8 MiB / 96 MiB) and
     deterministic AVB hash footers, optionally emitting vbmeta.img (--flags 3).
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CMDLINE = "rdinit=/init panic=15 mem_sleep_default=s2idle loglevel=4 tb378fc.stage=1"
DEBUG_CMDLINE = "tb378fc.debug=1 log_buf_len=2M printk.time=1"

# Verified TB378FC partition capacities (bytes)
DEFAULT_PART_SIZES = {
    "boot": 100663296,         # 96 MiB
    "init_boot": 8388608,      # 8 MiB
    "vendor_boot": 100663296,  # 96 MiB
}


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def avb_cmd(avbtool_path: Path) -> list[str]:
    if avbtool_path.suffix == ".py":
        return [sys.executable, str(avbtool_path.resolve())]
    return [str(avbtool_path)]


def dtb_has_abl_sink(dtb_path: Path) -> bool:
    blob = dtb_path.read_bytes()
    return b"__symbols__" in blob and b"qcom,msm-id" in blob


def sign_standalone(
    out_dir: Path,
    signed_dir: Path,
    key: Path,
    avbtool_path: Path,
    os_version: str,
    os_patch_level: str,
    part_sizes: dict[str, int],
    with_vbmeta: bool,
) -> dict:
    tool = avb_cmd(avbtool_path)
    signed_dir.mkdir(parents=True, exist_ok=False)
    partitions = {}
    for name in ("boot", "init_boot", "vendor_boot"):
        src = out_dir / f"{name}.img"
        dst = signed_dir / f"{name}.img"
        shutil.copyfile(src, dst)
        size = part_sizes[name]
        if dst.stat().st_size > size:
            raise SystemExit(f"{name}.img ({dst.stat().st_size} B) exceeds partition size {size} B")
        salt = hashlib.sha256(
            b"tb378fc-avb-salt\x00" + name.encode() + b"\x00" + bytes.fromhex(digest(src))
        ).hexdigest()
        subprocess.run(
            tool + [
                "add_hash_footer",
                "--image", str(dst),
                "--partition_name", name,
                "--partition_size", str(size),
                "--rollback_index", "0",
                "--salt", salt,
                "--algorithm", "SHA256_RSA4096",
                "--key", str(key.resolve()),
                "--prop", f"com.android.build.{name}.os_version:{os_version}",
                "--prop", f"com.android.build.{name}.security_patch:{os_patch_level}",
            ],
            check=True,
        )
        subprocess.run(
            tool + ["verify_image", "--image", str(dst), "--key", str(key.resolve())],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        partitions[name] = {"sha256": digest(dst), "bytes": dst.stat().st_size}

    if with_vbmeta:
        vbmeta = signed_dir / "vbmeta.img"
        subprocess.run(
            tool + [
                "make_vbmeta_image",
                "--output", str(vbmeta),
                "--algorithm", "SHA256_RSA4096",
                "--key", str(key.resolve()),
                "--padding_size", "65536",
                "--flags", "3",
                "--include_descriptors_from_image", str(signed_dir / "boot.img"),
                "--include_descriptors_from_image", str(signed_dir / "init_boot.img"),
                "--include_descriptors_from_image", str(signed_dir / "vendor_boot.img"),
            ],
            check=True,
        )
        subprocess.run(
            tool + ["verify_image", "--image", str(vbmeta), "--key", str(key.resolve())],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        partitions["vbmeta"] = {"sha256": digest(vbmeta), "bytes": vbmeta.stat().st_size}
    return partitions


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ["kernel", "dtb", "initramfs", "mkbootimg", "output"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--stock", type=Path, default=None,
                   help="Directory with stock boot/init_boot/vendor_boot.img (preserves stock sizes & rollback)")
    p.add_argument("--key", type=Path, default=None, help="RSA-4096 private key PEM for AVB signing")
    p.add_argument("--avbtool", type=Path, default=None, help="Path to avbtool or avbtool.py")
    p.add_argument("--with-vbmeta", action="store_true",
                   help="Also build AVB vbmeta.img (--flags 3) bundling boot/init_boot/vendor_boot descriptors")
    p.add_argument("--unsigned", action="store_true",
                   help="Build unsigned Header v4 images only (for offline inspection/CI; do not flash raw)")
    p.add_argument("--cmdline", default=CMDLINE)
    p.add_argument("--root", default="", help="Root specifier for tb378fc.root= (e.g. UUID=... or PARTLABEL=ubuntu)")
    p.add_argument("--debug", action="store_true", help="Enable opt-in tb378fc.debug=1 USB diag channel")
    p.add_argument("--cmdline-extra", default="", help="Additional kernel command-line parameters")
    p.add_argument("--os-version", default="15.0.0")
    p.add_argument("--os-patch-level", default="2026-07")
    p.add_argument("--boot-size", type=int, default=DEFAULT_PART_SIZES["boot"])
    p.add_argument("--init-boot-size", type=int, default=DEFAULT_PART_SIZES["init_boot"])
    p.add_argument("--vendor-boot-size", type=int, default=DEFAULT_PART_SIZES["vendor_boot"])
    a = p.parse_args()

    if not a.unsigned and (a.key is None or a.avbtool is None):
        p.error("--key and --avbtool are required unless --unsigned is specified")

    parts = [a.cmdline.strip()]
    if a.root.strip():
        parts.append(f"tb378fc.root={a.root.strip()}")
    if a.debug:
        parts.append(DEBUG_CMDLINE)
    if a.cmdline_extra.strip():
        parts.append(a.cmdline_extra.strip())
    cmdline = " ".join(part for part in parts if part)

    a.output.mkdir(parents=True, exist_ok=False)
    trailer = b"TRAILER!!!\0"
    empty = b"070701" + b"".join(b"%08X" % v for v in (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(trailer), 0)) + trailer
    empty += b"\0" * (-len(empty) % 4)
    ramdisk = a.output / "empty.cpio.gz"
    ramdisk.write_bytes(gzip.compress(empty, mtime=0))

    mk = [sys.executable, str(a.mkbootimg.resolve()), "--header_version", "4", "--pagesize", "4096"]
    metadata = ["--os_version", a.os_version, "--os_patch_level", a.os_patch_level]
    subprocess.run(
        mk + metadata + ["--kernel", str(a.kernel.resolve()), "--cmdline", cmdline,
                         "--output", str(a.output / "boot.img")],
        check=True,
    )
    subprocess.run(
        mk + metadata + ["--ramdisk", str(ramdisk), "--output", str(a.output / "init_boot.img")],
        check=True,
    )
    subprocess.run(
        mk + ["--vendor_ramdisk", str(a.initramfs.resolve()), "--dtb", str(a.dtb.resolve()),
              "--vendor_cmdline", "", "--base", "0", "--kernel_offset", "0x8000",
              "--ramdisk_offset", "0x1000000", "--tags_offset", "0x100", "--dtb_offset", "0x1f00000",
              "--vendor_boot", str(a.output / "vendor_boot.img")],
        check=True,
    )

    abl_sink = dtb_has_abl_sink(a.dtb.resolve())
    signed = a.output / "signed"
    if a.unsigned:
        layout = "GKI-v4-unsigned-offline"
        part_manifest = {
            n: {
                "sha256": digest(a.output / f"{n}.img"),
                "bytes": (a.output / f"{n}.img").stat().st_size,
            }
            for n in ("boot", "init_boot", "vendor_boot")
        }
    elif a.stock is not None:
        layout = "GKI-v4-AVB-stock-matched"
        subprocess.run(
            [sys.executable, str(ROOT / "third_party/oneplus/tools/sign-boot-triplet.py"),
             "--triplet", str(a.output), "--stock", str(a.stock.resolve()), "--key", str(a.key.resolve()),
             "--avbtool", str(a.avbtool.resolve()), "--output", str(signed)],
            check=True,
        )
        part_manifest = {
            n: {
                "sha256": digest(signed / f"{n}.img"),
                "bytes": (signed / f"{n}.img").stat().st_size,
            }
            for n in ("boot", "init_boot", "vendor_boot")
        }
        if a.with_vbmeta:
            tool = avb_cmd(a.avbtool)
            vbmeta = signed / "vbmeta.img"
            subprocess.run(
                tool + [
                    "make_vbmeta_image",
                    "--output", str(vbmeta),
                    "--algorithm", "SHA256_RSA4096",
                    "--key", str(a.key.resolve()),
                    "--padding_size", "65536",
                    "--flags", "3",
                    "--include_descriptors_from_image", str(signed / "boot.img"),
                    "--include_descriptors_from_image", str(signed / "init_boot.img"),
                    "--include_descriptors_from_image", str(signed / "vendor_boot.img"),
                ],
                check=True,
            )
            part_manifest["vbmeta"] = {"sha256": digest(vbmeta), "bytes": vbmeta.stat().st_size}
    else:
        layout = "GKI-v4-AVB-standalone"
        part_manifest = sign_standalone(
            a.output,
            signed,
            a.key,
            a.avbtool,
            a.os_version,
            a.os_patch_level,
            {
                "boot": a.boot_size,
                "init_boot": a.init_boot_size,
                "vendor_boot": a.vendor_boot_size,
            },
            a.with_vbmeta,
        )

    result = {
        "layout": layout,
        "header_version": 4,
        "device": "Lenovo TB378FC",
        "dtb_has_abl_sink": abl_sink,
        "cmdline": cmdline,
        "debug_enabled": a.debug,
        "os_version": a.os_version,
        "os_patch_level": a.os_patch_level,
        "inputs": {n: digest(getattr(a, n)) for n in ["kernel", "dtb", "initramfs"]},
        "partitions": part_manifest,
    }
    (a.output / "build.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    if not abl_sink:
        print(
            "NOTE: DTB has no ABL overlay sink (__symbols__/qcom,msm-id missing). "
            "Wrap the DTB with your stock vendor_boot/dtbo via "
            "third_party/oneplus/tools/build-erhai-abl-symbols.py before flashing to a physical TB378FC.",
            file=sys.stderr,
        )
    if a.unsigned:
        print("Built unsigned GKI v4 images (offline inspection only):", a.output)
    else:
        print("Built AVB-signed GKI v4 images:", signed)
        print("First-time slot flash (after backing up stock triplet): "
              "fastboot flash boot_<slot> boot.img && fastboot flash init_boot_<slot> init_boot.img "
              "&& fastboot flash vendor_boot_<slot> vendor_boot.img"
              + (" && fastboot flash vbmeta_<slot> vbmeta.img" if a.with_vbmeta else ""))
        print("Kernel-only retest (when DTB & initramfs are unchanged): "
              "fastboot flash boot_<slot> boot.img && fastboot reboot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
