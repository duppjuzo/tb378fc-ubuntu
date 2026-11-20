#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Boot-smoke the compiled TB378FC ARM64 kernel + initramfs live in QEMU (virt).

Validates offline without physical hardware:
  1. Linux 7.4 ARM64 Image boots cleanly to userspace with kernel taint == 0.
  2. AppArmor LSM is active and Rust Android Binder (binderfs) mounts cleanly.
  3. initramfs/init resolves tb378fc.root=UUID=..., stages /run/initramfs/shutdown,
     copies /etc/tb378fc-build-id, and switches root cleanly.
  4. Direct ext4 boot keeps debug services strictly OFF without tb378fc.debug=1.
  5. SquashFS + OverlayFS boot with tb378fc.debug=1 mounts both lower layers +
     writable upperdir and activates the gated debug channel.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

BUSYBOX_LINKS = (
    "sh", "mount", "umount", "cat", "ls", "grep", "ps", "poweroff", "reboot",
    "mkdir", "uname", "sleep", "awk", "test", "[", "echo", "touch", "rm",
)


def make_smoke_init(scenario: str) -> str:
    return f"""#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
echo "=== TB378FC_QEMU_SMOKE_START scenario={scenario} ==="
echo "SMOKE_UNAME=$(uname -r)"
echo "SMOKE_TAINTED=$(cat /proc/sys/kernel/tainted 2>/dev/null || echo unknown)"
mount -t securityfs securityfs /sys/kernel/security 2>/dev/null || true
echo "SMOKE_LSM=$(cat /sys/kernel/security/lsm 2>/dev/null || echo none)"
echo "SMOKE_BUILD_ID=$(cat /etc/tb378fc-build-id 2>/dev/null || echo missing)"
echo "SMOKE_ROOT_STAGE=$(cat /run/evidence/root-stage.txt 2>/dev/null || echo missing)"

# Verify Rust Android Binder (binderfs) is functional in the live kernel
mkdir -p /dev/binderfs
if mount -t binder binder /dev/binderfs 2>&1; then
    if [ -c /dev/binderfs/binder-control ]; then
        echo "SMOKE_BINDER=OK"
    else
        echo "SMOKE_BINDER=NO_CONTROL_NODE"
    fi
else
    echo "SMOKE_BINDER=MOUNT_FAILED"
fi

# Verify shutdown pivot staging
if [ -x /run/initramfs/shutdown ] && [ -x /run/initramfs/bin/busybox ]; then
    echo "SMOKE_SHUTDOWN_STAGED=OK"
else
    echo "SMOKE_SHUTDOWN_STAGED=MISSING"
fi

# Verify debug channel gating
if [ -e /run/evidence/usb-setup.txt ]; then
    echo "SMOKE_DEBUG_CHANNEL=ACTIVE"
else
    echo "SMOKE_DEBUG_CHANNEL=DISABLED"
fi

# Check active httpd process (should never leak past switch_root)
if ps | grep -q '[h]ttpd'; then
    echo "SMOKE_HTTPD_LEAK=YES"
else
    echo "SMOKE_HTTPD_LEAK=NO"
fi

# Check root filesystem type + overlay layer markers if applicable
ROOT_FS=$(awk '$2 == "/" {{print $3}}' /proc/mounts | tail -n 1)
echo "SMOKE_ROOT_FSTYPE=$ROOT_FS"
if [ -f /etc/layer-minimal ] && [ -f /etc/layer-standard ]; then
    echo "SMOKE_OVERLAY_LAYERS=OK"
    if echo ok >/tmp/overlay-write-test 2>/dev/null && [ "$(cat /tmp/overlay-write-test)" = "ok" ]; then
        echo "SMOKE_OVERLAY_WRITABLE=OK"
    else
        echo "SMOKE_OVERLAY_WRITABLE=FAIL"
    fi
else
    echo "SMOKE_OVERLAY_LAYERS=NONE"
fi

echo "=== TB378FC_QEMU_SMOKE_END scenario={scenario} ==="
poweroff -f
sleep 5
"""


def populate_minimal_root(root: Path, busybox: Path, scenario: str) -> None:
    for d in ("bin", "sbin", "etc", "dev", "proc", "sys", "run", "tmp"):
        (root / d).mkdir(parents=True, exist_ok=True)
    bb_dst = root / "bin/busybox"
    shutil.copy2(busybox, bb_dst)
    bb_dst.chmod(0o755)
    for link in BUSYBOX_LINKS:
        dst = root / "bin" / link
        if not dst.exists():
            dst.symlink_to("busybox")
    init_path = root / "sbin/init"
    init_path.write_text(make_smoke_init(scenario), encoding="utf-8")
    init_path.chmod(0o755)


def build_direct_disk(tmp: Path, busybox: Path) -> tuple[Path, str]:
    root = tmp / "direct-root"
    populate_minimal_root(root, busybox, "direct")
    disk = tmp / "direct.ext4"
    vol_uuid = str(uuid.uuid4())
    subprocess.run(["truncate", "-s", "32M", str(disk)], check=True)
    subprocess.run(
        ["mkfs.ext4", "-q", "-F", "-U", vol_uuid, "-d", str(root), str(disk)],
        check=True,
    )
    return disk, vol_uuid


def build_overlay_disk(tmp: Path, busybox: Path) -> tuple[Path, str]:
    layer_min = tmp / "layer-minimal"
    populate_minimal_root(layer_min, busybox, "overlay-debug")
    (layer_min / "etc/layer-minimal").write_text("minimal\n", encoding="utf-8")

    layer_std = tmp / "layer-standard"
    (layer_std / "etc").mkdir(parents=True, exist_ok=True)
    (layer_std / "etc/layer-standard").write_text("standard\n", encoding="utf-8")

    host_root = tmp / "overlay-ext4"
    for d in ("layers", "upper", "work"):
        (host_root / d).mkdir(parents=True, exist_ok=True)
    (host_root / ".tb378fc-root").write_text("tb378fc-qemu-smoke\n", encoding="utf-8")

    subprocess.run(
        ["mksquashfs", str(layer_min), str(host_root / "layers/minimal.squashfs"), "-quiet", "-noappend", "-comp", "gzip"],
        check=True,
    )
    subprocess.run(
        ["mksquashfs", str(layer_std), str(host_root / "layers/minimal.standard.squashfs"), "-quiet", "-noappend", "-comp", "gzip"],
        check=True,
    )

    disk = tmp / "overlay.ext4"
    vol_uuid = str(uuid.uuid4())
    subprocess.run(["truncate", "-s", "32M", str(disk)], check=True)
    subprocess.run(
        ["mkfs.ext4", "-q", "-F", "-U", vol_uuid, "-d", str(host_root), str(disk)],
        check=True,
    )
    return disk, vol_uuid


def run_qemu(image: Path, initramfs: Path, disk: Path, append: str, timeout: int) -> tuple[int, str, float]:
    cmd = [
        "qemu-system-aarch64",
        "-M", "virt",
        "-cpu", "cortex-a57",
        "-smp", "2",
        "-m", "1024",
        "-nographic",
        "-no-reboot",
        "-kernel", str(image),
        "-initrd", str(initramfs),
        "-drive", f"file={disk},format=raw,if=virtio",
        "-append", append,
    ]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    elapsed = round(time.monotonic() - t0, 2)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace"), elapsed


def parse_markers(log: str) -> dict[str, str]:
    markers = {}
    for line in log.splitlines():
        line = line.strip()
        if line.startswith("SMOKE_") and "=" in line:
            k, v = line.split("=", 1)
            markers[k] = v
    return markers


def verify_scenario(name: str, rc: int, log: str, expected: dict[str, str], contains: dict[str, str]) -> dict:
    markers = parse_markers(log)
    failures = []
    if f"=== TB378FC_QEMU_SMOKE_END scenario={name} ===" not in log:
        failures.append("missing smoke end marker")
    if "Kernel panic" in log or "Oops:" in log or "BUG:" in log:
        failures.append("kernel oops/bug/panic detected in serial log")
    for k, want in expected.items():
        got = markers.get(k)
        if got != want:
            failures.append(f"{k}: expected {want!r}, got {got!r}")
    for k, sub in contains.items():
        got = markers.get(k, "")
        if sub not in got:
            failures.append(f"{k}: expected substring {sub!r} in {got!r}")
    return {
        "scenario": name,
        "ok": len(failures) == 0,
        "qemu_returncode": rc,
        "markers": markers,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=Path("/root/tb378fc-work/out/kernel-tb378fc/arch/arm64/boot/Image"))
    parser.add_argument("--initramfs", type=Path, default=Path("/root/tb378fc-work/out/initramfs-tb378fc-v7.4.cpio.gz"))
    parser.add_argument("--busybox", type=Path, default=Path("/root/tb378fc-work/cache/tools/busybox-arm64"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--log-dir", type=Path, default=None)
    args = parser.parse_args()

    for label, path in (("Image", args.image), ("initramfs", args.initramfs), ("busybox", args.busybox)):
        if not path.is_file():
            raise SystemExit(f"Missing {label}: {path}")

    results = []
    with tempfile.TemporaryDirectory(prefix="tb378fc-qemu-smoke-") as td:
        tmp = Path(td)

        # Scenario 1: Direct ext4 boot WITHOUT tb378fc.debug=1
        disk1, uuid1 = build_direct_disk(tmp, args.busybox)
        append1 = f"console=ttyAMA0 rdinit=/init tb378fc.root=UUID={uuid1} panic=5 loglevel=4"
        rc1, log1, sec1 = run_qemu(args.image, args.initramfs, disk1, append1, args.timeout)
        r1 = verify_scenario(
            "direct",
            rc1,
            log1,
            expected={
                "SMOKE_TAINTED": "0",
                "SMOKE_BINDER": "OK",
                "SMOKE_SHUTDOWN_STAGED": "OK",
                "SMOKE_DEBUG_CHANNEL": "DISABLED",
                "SMOKE_HTTPD_LEAK": "NO",
                "SMOKE_ROOT_FSTYPE": "ext4",
            },
            contains={
                "SMOKE_UNAME": "7.4.0-rc2-tb378fc",
                "SMOKE_LSM": "apparmor",
                "SMOKE_ROOT_STAGE": "TB378FC_ROOT_READY",
            },
        )
        r1["elapsed_sec"] = sec1
        results.append(r1)
        if args.log_dir:
            args.log_dir.mkdir(parents=True, exist_ok=True)
            (args.log_dir / "qemu-smoke-direct.log").write_text(log1, encoding="utf-8")

        # Scenario 2: SquashFS + OverlayFS boot WITH tb378fc.debug=1
        disk2, uuid2 = build_overlay_disk(tmp, args.busybox)
        append2 = f"console=ttyAMA0 rdinit=/init tb378fc.root=UUID={uuid2} tb378fc.debug=1 panic=5 loglevel=4"
        rc2, log2, sec2 = run_qemu(args.image, args.initramfs, disk2, append2, args.timeout)
        r2 = verify_scenario(
            "overlay-debug",
            rc2,
            log2,
            expected={
                "SMOKE_TAINTED": "0",
                "SMOKE_BINDER": "OK",
                "SMOKE_SHUTDOWN_STAGED": "OK",
                "SMOKE_DEBUG_CHANNEL": "ACTIVE",
                "SMOKE_HTTPD_LEAK": "NO",
                "SMOKE_ROOT_FSTYPE": "overlay",
                "SMOKE_OVERLAY_LAYERS": "OK",
                "SMOKE_OVERLAY_WRITABLE": "OK",
            },
            contains={
                "SMOKE_UNAME": "7.4.0-rc2-tb378fc",
                "SMOKE_LSM": "apparmor",
                "SMOKE_ROOT_STAGE": "TB378FC_ROOT_READY",
            },
        )
        r2["elapsed_sec"] = sec2
        results.append(r2)
        if args.log_dir:
            (args.log_dir / "qemu-smoke-overlay-debug.log").write_text(log2, encoding="utf-8")

    summary = {
        "scope": "OFFLINE_QEMU_ARM64_VIRT_SMOKE_NOT_PHYSICAL_HARDWARE",
        "all_passed": all(r["ok"] for r in results),
        "scenarios": results,
    }
    if args.log_dir:
        (args.log_dir / "qemu-smoke-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    if not summary["all_passed"]:
        print("\n--- FAILURE DIAGNOSTICS ---", file=sys.stderr)
        if not r1["ok"]:
            print("=== direct log tail ===\n" + "\n".join(log1.splitlines()[-40:]), file=sys.stderr)
        if not r2["ok"]:
            print("=== overlay-debug log tail ===\n" + "\n".join(log2.splitlines()[-40:]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
