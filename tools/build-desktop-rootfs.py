#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Assemble a ready-to-flash Ubuntu 26.04 LTS ARM64 Desktop RootFS for Lenovo TB378FC.

Merges the pinned Ubuntu 26.04 Desktop ARM64 SquashFS layers via OverlayFS,
installs all TB378FC packages (unified Linux 7.4 kernel, GCC 14 Mesa 26.2.6 with
Adreno 830/830v1 Freedreno + Turnip, Qualcomm Iris VA-API, video defaults,
Electron/Wayland environment configs, ALSA UCM2 audio, and device-support),
stages redistributable uncompressed upstream linux-firmware for Adreno 830 &
VPU35 reference, runs in-chroot ARM64 ldd/environment.d/dpkg self-verification,
and packs a zstd-compressed rootfs.squashfs image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
DESKTOP_LOCK = REPO / "packaging/desktop/source.json"

OPEN_FIRMWARE_FILES = (
    "qcom/gen80000_sqe.fw",
    "qcom/gen80000_gmu.bin",
    "qcom/gen80000_aqe.fw",
    "qcom/sm8750/gen80000_zap.mbn",
    "qcom/vpu/vpu35_p4_s7.mbn",
)

EXTRA_DESKTOP_PACKAGES = (
    "gstreamer1.0-plugins-good",
    "gstreamer1.0-plugins-bad",
    "gstreamer1.0-libav",
    "vainfo",
    "vulkan-tools",
    "mesa-utils",
    "openssh-server",
    "zstd",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(map(str, cmd)), flush=True)
    return subprocess.run(list(map(str, cmd)), check=True, **kwargs)


def chroot_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "LC_ALL": "C.UTF-8",
        "DEBIAN_FRONTEND": "noninteractive",
        "FLASH_KERNEL_SKIP": "1",
        "QEMU_CPU": "max,pauth-impdef=on",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    })
    return env


def ensure_qemu_binfmt() -> None:
    reg = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    if reg.exists():
        return
    if shutil.which("update-binfmts"):
        subprocess.run(["update-binfmts", "--enable", "qemu-aarch64"], check=False)
    if not reg.exists():
        Path("/proc/sys/fs/binfmt_misc/register").write_text(
            r":qemu-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00:"
            r"\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:"
            r"/usr/bin/qemu-aarch64-static:F",
            encoding="utf-8",
        )


def extract_iso_layers(iso: Path, layers_dir: Path, lock: dict) -> tuple[Path, Path]:
    layers_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    for spec in lock["layers"]:
        iso_member = "/" + spec["path"].lstrip("/")
        dst = layers_dir / Path(spec["path"]).name
        if dst.is_file() and dst.stat().st_size == spec["bytes"] and sha256(dst) == spec["sha256"]:
            print(f"[CACHE] Verified layer {dst.name}")
        else:
            if dst.exists():
                dst.unlink()
            run(["xorriso", "-osirrox", "on", "-indev", str(iso), "-extract", iso_member, str(dst)])
            dst.chmod(0o644)
            got = sha256(dst)
            if got != spec["sha256"]:
                raise SystemExit(f"Layer SHA-256 mismatch for {dst.name}: expected {spec['sha256']}, got {got}")
        extracted.append(dst)
    return extracted[0], extracted[1]


def stage_open_firmware(root: Path, open_fw_dir: Path) -> list[str]:
    staged = []
    fw_dst_root = root / "lib/firmware"
    for rel in OPEN_FIRMWARE_FILES:
        src = open_fw_dir / rel
        if not src.is_file():
            raise SystemExit(f"Missing redistributable linux-firmware file: {src}")
        dst = fw_dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        dst.chmod(0o644)
        staged.append(rel)
    vpu_link = fw_dst_root / "qcom/vpu/vpu35_p4.mbn"
    if vpu_link.exists() or vpu_link.is_symlink():
        vpu_link.unlink()
    vpu_link.symlink_to("vpu35_p4_s7.mbn")

    tb_dir = fw_dst_root / "qcom/sm8750/tb378fc"
    tb_dir.mkdir(parents=True, exist_ok=True)
    (tb_dir / "README.txt").write_text(
        "Lenovo TB378FC vendor-signed firmware directory (not redistributed in Git/image).\n"
        "After extracting stock firmware from your physical TB378FC tablet, run:\n"
        "  sudo tb378fc-install-firmware --source-dir /path/to/extracted-stock-fw\n"
        "Required vendor-signed files:\n"
        "  - qcom/sm8750/tb378fc/vpu35_4v.mbn      (Iris VPU 3.5 hardware video decode/encode)\n"
        "  - qcom/sm8750/tb378fc/gen80000_zap.mbn  (Adreno 830 ZAP shader signed for Lenovo TZ)\n"
        "  - qcom/sm8750/tb378fc/adsp.mdt + adsp_dtb.mdt + adsp.b* (ADSP audio/sensors)\n"
        "  - novatek/tb378fc.bin                   (NT36536 SPI touchscreen firmware)\n"
        "  - aw88480_acf.bin                       (AW88480 quad speaker amp profile)\n"
        "  - ath12k/WCN7860/hw2.0/* & qca/{gng,brh}bt* (WCN7860 Wi-Fi 7 & Bluetooth firmware)\n",
        encoding="utf-8",
    )
    return staged


def verify_chroot(root: Path, kernel_release: str) -> dict:
    env = chroot_env()

    # 1. dpkg audit must be clean
    audit = subprocess.check_output(["chroot", str(root), "dpkg", "--audit"], text=True, env=env).strip()
    if audit:
        raise SystemExit(f"dpkg --audit reported issues:\n{audit}")

    # 2. Verify environment.d generator resolves Electron/Wayland & Iris VA-API variables
    gen_out = subprocess.check_output(
        ["chroot", str(root), "/usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator"],
        text=True,
        env=env,
    )
    env_map = {}
    for line in gen_out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            parts = shlex.split(v)
            env_map[k] = parts[0] if len(parts) == 1 else v
    required_env = {
        "ELECTRON_OZONE_PLATFORM_HINT": "auto",
        "OZONE_PLATFORM_HINT": "auto",
        "NIXOS_OZONE_WL": "1",
        "MOZ_ENABLE_WAYLAND": "1",
        "QT_QPA_PLATFORM": "wayland;xcb",
        "SDL_VIDEODRIVER": "wayland,x11",
        "GST_VA_ALL_DRIVERS": "1",
    }
    for k, want in required_env.items():
        if env_map.get(k) != want:
            raise SystemExit(f"environment.d check failed for {k}: expected {want!r}, got {env_map.get(k)!r}")
    if "vah264dec:512" not in env_map.get("GST_PLUGIN_FEATURE_RANK", ""):
        raise SystemExit(f"Missing vah264dec rank in GST_PLUGIN_FEATURE_RANK: {env_map.get('GST_PLUGIN_FEATURE_RANK')!r}")

    # 3. Verify ldd dynamic linker closure inside real Ubuntu 26.04 ARM64 rootfs (0 'not found')
    ldd_targets = (
        "/usr/lib/tb378fc/mesa/lib/libEGL_mesa.so.0",
        "/usr/lib/tb378fc/mesa/lib/libGLX_mesa.so.0",
        "/usr/lib/tb378fc/mesa/lib/libgallium-26.2.6.so",
        "/usr/lib/tb378fc/mesa/lib/libvulkan_freedreno.so",
        "/usr/lib/aarch64-linux-gnu/dri/v4l2_drv_video.so",
        "/usr/lib/aarch64-linux-gnu/libcamera.so.0.7",
        "/usr/lib/aarch64-linux-gnu/libcamera-base.so.0.7",
        "/usr/lib/aarch64-linux-gnu/libcamera/ipa/ipa_soft_simple.so",
        "/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstlibcamera.so",
        "/usr/libexec/libcamera/v4l2-compat.so",
        "/usr/libexec/libcamera/soft_ipa_proxy",
    )
    ldd_results = {}
    for lib in ldd_targets:
        out = subprocess.check_output(["chroot", str(root), "ldd", lib], text=True, env=env)
        missing = [ln.strip() for ln in out.splitlines() if "not found" in ln]
        if missing:
            raise SystemExit(f"Missing shared libraries for {lib} inside rootfs:\n" + "\n".join(missing))
        ldd_results[lib] = len([ln for ln in out.splitlines() if "=>" in ln])

    # 4. Verify Adreno 830 strings, Turnip ICD, Iris VA-API symlink, and kernel modules.dep
    assert (root / "usr/lib/aarch64-linux-gnu/dri/msm_drv_video.so").is_symlink()
    assert (root / "usr/lib/tb378fc/mesa/share/vulkan/icd.d/freedreno_icd.aarch64.json").is_file()
    assert (root / "usr/lib/tb378fc/mesa/share/glvnd/egl_vendor.d/50_mesa.json").is_file()
    assert (root / f"lib/modules/{kernel_release}/modules.dep").is_file()
    assert (root / "lib/firmware/qcom/gen80000_sqe.fw").is_file()
    assert (root / "lib/firmware/qcom/gen80000_gmu.bin").is_file()
    assert (root / "lib/firmware/qcom/vpu/vpu35_p4_s7.mbn").is_file()
    assert (root / "usr/lib/aarch64-linux-gnu/libcamera/ipa/ipa_soft_simple.so.sign").is_file()
    assert (root / "usr/share/libcamera/ipa/simple/ov13b10.yaml").is_file()
    assert (root / f"lib/modules/{kernel_release}/kernel/drivers/media/i2c/dw9807-vcm.ko").is_file()
    assert (root / "etc/udev/rules.d/60-persistent-v4l.rules").is_file()

    # 5. Ensure no machine-specific state leaked
    mid = root / "etc/machine-id"
    if mid.exists():
        content = mid.read_text(encoding="utf-8").strip()
        if content and content != "uninitialized":
            raise SystemExit(f"Unexpected initialized /etc/machine-id in rootfs: {content!r}")

    return {
        "dpkg_audit": "CLEAN",
        "environment_d": {k: env_map.get(k) for k in (*required_env.keys(), "GST_PLUGIN_FEATURE_RANK")},
        "ldd_resolved_deps": ldd_results,
        "kernel_modules_dep": f"/lib/modules/{kernel_release}/modules.dep",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--debs-dir", type=Path, required=True)
    parser.add_argument("--open-firmware-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--keep-layers", action="store_true", help="Keep unpacked minimal/standard dirs for faster rebuilds")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("build-desktop-rootfs.py must run as root inside WSL2/Linux ext4")

    ensure_qemu_binfmt()
    lock = json.loads(DESKTOP_LOCK.read_text(encoding="utf-8"))
    iso = args.iso.resolve()
    if not iso.is_file():
        raise SystemExit(f"Missing Ubuntu Desktop ISO: {iso}")

    work = args.work_dir.resolve()
    out_dir = args.output_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    debs = sorted(args.debs_dir.resolve().glob("tb378fc-*.deb"))
    if len(debs) < 8:
        raise SystemExit(f"Expected 7 tb378fc-*.deb packages in {args.debs_dir}, found {[d.name for d in debs]}")

    # Identify kernel release from tb378fc-kernel-image deb
    kernel_debs = [d for d in debs if d.name.startswith("tb378fc-kernel-image_")]
    if len(kernel_debs) != 1:
        raise SystemExit(f"Expected exactly 1 tb378fc-kernel-image deb, found {kernel_debs}")
    kver_pkg = subprocess.check_output(["dpkg-deb", "-f", str(kernel_debs[0]), "Version"], text=True).strip()
    kernel_release = kver_pkg.rsplit("-", 1)[0]

    min_sq, std_sq = extract_iso_layers(iso, work / "iso-layers", lock)
    min_dir = work / "minimal"
    std_dir = work / "standard"
    if not (min_dir / ".unpacked").exists():
        shutil.rmtree(min_dir, ignore_errors=True)
        run(["unsquashfs", "-no-progress", "-processors", str(args.jobs), "-d", str(min_dir), str(min_sq)])
        (min_dir / ".unpacked").write_text("ok\n", encoding="utf-8")
    if not (std_dir / ".unpacked").exists():
        shutil.rmtree(std_dir, ignore_errors=True)
        run(["unsquashfs", "-no-progress", "-processors", str(args.jobs), "-d", str(std_dir), str(std_sq)])
        (std_dir / ".unpacked").write_text("ok\n", encoding="utf-8")

    root = work / "root"
    upper = work / "upper"
    ovl_work = work / "ovl-work"
    for d in (root, upper, ovl_work):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    mounted_ovl = False
    mounted_binds = []
    resolv_backup_link = None
    resolv_path = root / "etc/resolv.conf"
    policy_diverted = False
    env = chroot_env()

    try:
        run([
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={std_dir}:{min_dir},upperdir={upper},workdir={ovl_work}",
            str(root),
        ])
        mounted_ovl = True

        # Remove marker files if visible in overlay
        for marker in (root / ".unpacked",):
            if marker.exists():
                marker.unlink()

        for target_rel, fstype in (("proc", "proc"), ("sys", "sysfs"), ("dev/pts", "devpts")):
            mp = root / target_rel
            mp.mkdir(parents=True, exist_ok=True)
            run(["mount", "-t", fstype, fstype, str(mp)])
            mounted_binds.append(mp)

        # Temporary DNS for chroot APT dependency resolution
        if resolv_path.is_symlink():
            resolv_backup_link = os.readlink(resolv_path)
            resolv_path.unlink()
        elif resolv_path.exists():
            resolv_path.unlink()
        shutil.copyfile("/etc/resolv.conf", resolv_path)

        # Inhibit service starts during chroot package installation
        run([
            "chroot", str(root), "dpkg-divert", "--local", "--add", "--rename",
            "--divert", "/usr/sbin/policy-rc.d.tb378fc-build", "/usr/sbin/policy-rc.d",
        ], env=env)
        policy_diverted = True
        policy_rc = root / "usr/sbin/policy-rc.d"
        policy_rc.write_text("#!/bin/sh\nexit 101\n", encoding="utf-8")
        policy_rc.chmod(0o755)

        # Remove ISO cdrom APT source so apt-get uses ports.ubuntu.com cleanly
        for cdrom in (root / "etc/apt/sources.list.d").glob("*cdrom*"):
            cdrom.unlink()

        # Copy all TB378FC debs into chroot staging dir
        deb_stage = root / ".tb378fc-debs"
        deb_stage.mkdir(parents=True, exist_ok=True)
        chroot_deb_args = []
        deb_records = []
        for d in debs:
            shutil.copy2(d, deb_stage / d.name)
            chroot_deb_args.append(f"/.tb378fc-debs/{d.name}")
            deb_records.append({"file": d.name, "size": d.stat().st_size, "sha256": sha256(d)})

        run(["chroot", str(root), "apt-get", "update", "-qq"], env=env)
        run([
            "chroot", str(root), "apt-get", "install", "-y", "--no-install-recommends",
            *chroot_deb_args,
            *EXTRA_DESKTOP_PACKAGES,
        ], env=env)

        # Remove Ubuntu generic kernels (TB378FC boots unified 7.4 kernel)
        generic_kernels = subprocess.check_output(
            ["chroot", str(root), "dpkg-query", "-W", "-f=${Package}\n",
             "linux-image-*", "linux-modules-*", "linux-headers-*", "linux-generic*"],
            text=True,
            env=env,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        generic_kernels = [p for p in generic_kernels if p and not p.startswith("tb378fc-")]
        if generic_kernels:
            run(["chroot", str(root), "apt-get", "purge", "-y", *generic_kernels], env=env)
        run(["chroot", str(root), "apt-get", "autoremove", "--purge", "-y"], env=env)
        run(["chroot", str(root), "apt-get", "clean"], env=env)

        # Generate kernel module dependency graph for our unified Linux 7.4 kernel
        run(["chroot", str(root), "depmod", "-a", kernel_release], env=env)

        # Stage redistributable uncompressed upstream Adreno 830 & VPU35 reference firmware
        staged_fw = stage_open_firmware(root, args.open_firmware_dir.resolve())

        # Remove policy-rc.d & temporary build files
        policy_rc.unlink()
        run([
            "chroot", str(root), "dpkg-divert", "--local", "--remove", "--rename",
            "--divert", "/usr/sbin/policy-rc.d.tb378fc-build", "/usr/sbin/policy-rc.d",
        ], env=env)
        policy_diverted = False
        shutil.rmtree(deb_stage)
        for p in (root / "var/lib/apt/lists").glob("*"):
            if p.is_file():
                p.unlink()

        # Restore /etc/resolv.conf symlink & clean machine-id for first-boot initialization
        if resolv_path.exists() or resolv_path.is_symlink():
            resolv_path.unlink()
        if resolv_backup_link:
            resolv_path.symlink_to(resolv_backup_link)
        mid = root / "etc/machine-id"
        if mid.exists():
            mid.write_text("uninitialized\n", encoding="utf-8")

        # Run full ARM64 in-chroot verification before unmounting!
        verify_report = verify_chroot(root, kernel_release)

        # Export full package inventory
        pkgs_txt = subprocess.check_output(
            ["chroot", str(root), "dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\t${Status}\n"],
            text=True,
            env=env,
        )
        (out_dir / "rootfs-packages.txt").write_text(pkgs_txt, encoding="utf-8")

        # Unmount virtual filesystems before mksquashfs
        for mp in reversed(mounted_binds):
            run(["umount", str(mp)])
        mounted_binds.clear()

        sq_out = out_dir / "ubuntu-26.04-tb378fc-arm64-rootfs.squashfs"
        if sq_out.exists():
            sq_out.unlink()
        run([
            "mksquashfs", str(root), str(sq_out),
            "-noappend", "-comp", "zstd", "-Xcompression-level", "12",
            "-processors", str(args.jobs), "-no-progress",
        ])

        manifest = {
            "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
            "ubuntu_release": lock["release"],
            "kernel_release": kernel_release,
            "rootfs_file": sq_out.name,
            "rootfs_bytes": sq_out.stat().st_size,
            "rootfs_sha256": sha256(sq_out),
            "installed_tb378fc_debs": deb_records,
            "staged_open_firmware": staged_fw,
            "verification": verify_report,
            "vendor_firmware_required_on_device": [
                "qcom/sm8750/tb378fc/vpu35_4v.mbn",
                "qcom/sm8750/tb378fc/gen80000_zap.mbn",
                "qcom/sm8750/tb378fc/adsp.mdt (+ adsp_dtb.mdt, adsp.b*)",
                "novatek/tb378fc.bin",
                "aw88480_acf.bin",
                "ath12k/WCN7860/hw2.0/* & qca/gngbt*",
            ],
        }
        (out_dir / "rootfs-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2), flush=True)
    finally:
        if policy_diverted:
            try:
                (root / "usr/sbin/policy-rc.d").unlink(missing_ok=True)
                subprocess.run([
                    "chroot", str(root), "dpkg-divert", "--local", "--remove", "--rename",
                    "--divert", "/usr/sbin/policy-rc.d.tb378fc-build", "/usr/sbin/policy-rc.d",
                ], check=False, env=env)
            except Exception:
                pass
        for mp in reversed(mounted_binds):
            subprocess.run(["umount", "-l", str(mp)], check=False)
        if mounted_ovl:
            subprocess.run(["umount", str(root)], check=False)
        shutil.rmtree(upper, ignore_errors=True)
        shutil.rmtree(ovl_work, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        if not args.keep_layers:
            shutil.rmtree(min_dir, ignore_errors=True)
            shutil.rmtree(std_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
