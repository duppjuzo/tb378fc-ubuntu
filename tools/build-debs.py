#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build rootless ARM64 Debian packages for Lenovo TB378FC.

Produces:
  - tb378fc-device-support_<ver>_arm64.deb  (systemd units, helpers, udev rules, symlinks)
  - tb378fc-audio_<ver>_arm64.deb           (compiled Lenovo-TB378FC-tplg.bin, ALSA UCM2,
                                             and PipeWire quad-speaker rotation service)
  - tb378fc-kernel-image_<krel>_arm64.deb   (optional, when --kernel-out is provided:
                                             /boot/vmlinuz, /boot/dtb, /lib/modules/<krel>)
  - tb378fc-device_<ver>_arm64.deb          (metapackage tying the board stack together)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
MAINTAINER = "TB378FC Maintainers <tb378fc@localhost>"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree_filtered(src: Path, dst: Path, exclude_prefixes=()):
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        if any(rel == pref or rel.startswith(pref + "/") for pref in exclude_prefixes):
            continue
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        target = dst / rel
        if path.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(os.readlink(path))
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def pack_deb(stage: Path, out_deb: Path):
    out_deb.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(out_deb)],
        check=True,
    )
    return out_deb


def write_control(stage: Path, fields: dict, conffiles=(), postinst=None):
    debian = stage / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}: {v}" for k, v in fields.items()]
    (debian / "control").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if conffiles:
        (debian / "conffiles").write_text("\n".join(sorted(conffiles)) + "\n", encoding="utf-8")
    if postinst:
        p = debian / "postinst"
        p.write_text(postinst, encoding="utf-8")
        p.chmod(0o755)


def collect_conffiles(stage: Path):
    etc = stage / "etc"
    if not etc.is_dir():
        return []
    result = []
    for p in sorted(etc.rglob("*")):
        if p.is_file() and not p.is_symlink():
            result.append("/" + p.relative_to(stage).as_posix())
    return result


def build_device_support(version: str, out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "pkg"
        stage.mkdir()
        tree = REPO / "packaging/device-support/tree"
        copy_tree_filtered(tree, stage, exclude_prefixes=("usr/share/alsa",))

        symlinks = json.loads((REPO / "packaging/device-support/symlinks.json").read_text(encoding="utf-8"))
        for raw_path, target in symlinks.items():
            link_rel = raw_path.lstrip("/")
            link_path = stage / link_rel
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            link_path.symlink_to(target)

        postinst = (
            "#!/bin/sh\n"
            "set -eu\n"
            'if [ "$1" = "configure" ]; then\n'
            "    rm -f /etc/udev/rules.d/82-tb378fc-dmaheap75.rules || true\n"
            "    if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then\n"
            "        glib-compile-schemas /usr/share/glib-2.0/schemas >/dev/null 2>&1 || true\n"
            "    fi\n"
            "    if [ -x /usr/share/gdm/generate-config ]; then\n"
            "        /usr/share/gdm/generate-config >/dev/null 2>&1 || true\n"
            "    fi\n"
            "    if command -v usermod >/dev/null 2>&1; then\n"
            "        awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ { print $1 }' /etc/passwd | while IFS= read -r u; do\n"
            '            [ -n "$u" ] && usermod -aG video,render "$u" 2>/dev/null || true\n'
            "        done\n"
            "    fi\n"
            "    if [ -f /etc/default/keyboard ]; then\n"
            "        sed -i -E 's/^XKBLAYOUT=\"default,([^\"]+)\"/XKBLAYOUT=\"\\1\"/; s/^XKBLAYOUT=\"([^\"]+),(default)\"/XKBLAYOUT=\"\\1\"/; s/^XKBLAYOUT=\"default\"/XKBLAYOUT=\"us\"/; s/^XKBVARIANT=\",([^\"]*)\"/XKBVARIANT=\"\\1\"/' /etc/default/keyboard 2>/dev/null || true\n"
            "    fi\n"
            "    if [ -f /etc/default/kdump-tools ]; then\n"
            "        sed -i 's/^USE_KDUMP=1/USE_KDUMP=0/' /etc/default/kdump-tools 2>/dev/null || true\n"
            "    fi\n"
            "    systemctl mask systemd-networkd-wait-online.service >/dev/null 2>&1 || true\n"
            "    systemctl disable systemd-networkd.service systemd-networkd.socket networkd-dispatcher.service NetworkManager-wait-online.service chrony-wait.service ModemManager.service spice-vdagentd.service spice-vdagentd.socket rsyslog.service syslog.socket kdump-tools.service cups.service cups.socket cups.path cups-browsed.service speech-dispatcherd.service avahi-daemon.service avahi-daemon.socket unattended-upgrades.service tb378fc-power-key.service tb378fc-volume-orientation.service >/dev/null 2>&1 || true\n    rm -f /etc/systemd/system/graphical.target.wants/tb378fc-power-key.service /etc/systemd/system/graphical.target.wants/tb378fc-volume-orientation.service || true\n"
            "    if [ -d /run/systemd/system ]; then\n"
            "        systemctl daemon-reload || true\n"
            "        systemd-tmpfiles --create /etc/tmpfiles.d/90-tb378fc-mglru.conf >/dev/null 2>&1 || true\n"
            "        udevadm control --reload-rules || true\n"
            "        udevadm trigger --subsystem-match=dma_heap --subsystem-match=media --subsystem-match=video4linux --subsystem-match=block || true\n"
            "        sysctl -p /etc/sysctl.d/90-tb378fc-vm.conf >/dev/null 2>&1 || true\n"
            "        modprobe v4l2loopback 2>/dev/null || true\n"
            "        [ -x /usr/local/sbin/tb378fc-hw-init ] && /usr/local/sbin/tb378fc-hw-init || true\n"
            "        systemctl enable fstrim.timer >/dev/null 2>&1 || true\n"
            "        systemctl try-restart systemd-journald.service systemd-oomd.service >/dev/null 2>&1 || true\n"
            "        systemctl stop tb378fc-power-key.service tb378fc-volume-orientation.service unattended-upgrades.service fwupd.service >/dev/null 2>&1 || true\n"
            "        systemctl restart tb378fc-thermal-guard.service || true\n"
            "        update-desktop-database /usr/local/share/applications >/dev/null 2>&1 || true\n"
            "        if systemctl is-active --quiet apparmor.service 2>/dev/null; then\n"
            "            systemctl reload apparmor.service >/dev/null 2>&1 || true\n"
            "        elif command -v apparmor_parser >/dev/null 2>&1 && [ -d /sys/kernel/security/apparmor ]; then\n"
            "            for _aa_prof in /etc/apparmor.d/*; do\n"
            "                [ -f \"$_aa_prof\" ] && apparmor_parser -r -W \"$_aa_prof\" >/dev/null 2>&1 || true\n"
            "            done\n"
            "        fi\n"
            "    fi\n" 
            "fi\n"
            "exit 0\n"
        )
        write_control(
            stage,
            {
                "Package": "tb378fc-device-support",
                "Version": version,
                "Architecture": "arm64",
                "Section": "admin",
                "Priority": "optional",
                "Maintainer": MAINTAINER,
                "Depends": "systemd, python3, udev",
                "Description": "Lenovo Xiaoxin Pad Pro GT 13 (TB378FC) userspace support\n"
                " Systemd units, dynamic seat helpers, udev rules, and suspend hooks\n"
                " for Lenovo TB378FC running mainline Linux 7.4+.",
            },
            conffiles=collect_conffiles(stage),
            postinst=postinst,
        )
        return pack_deb(stage, out_dir / f"tb378fc-device-support_{version}_arm64.deb")


def compile_audioreach_topology(work_dir: Path, dst_tplg: Path) -> None:
    m4_src = REPO / "packaging/audio/Lenovo-TB378FC.m4"
    m4_inc = REPO / "packaging/audio/include"
    conf_path = work_dir / "Lenovo-TB378FC.conf"
    decoded_path = work_dir / "Lenovo-TB378FC.decoded.conf"
    dst_tplg.parent.mkdir(parents=True, exist_ok=True)

    with conf_path.open("w", encoding="utf-8") as conf_out:
        subprocess.run(
            ["m4", "-I", str(m4_inc), str(m4_src)],
            stdout=conf_out,
            check=True,
        )
    subprocess.run(["alsatplg", "-c", str(conf_path), "-o", str(dst_tplg)], check=True)
    subprocess.run(["alsatplg", "-d", str(dst_tplg), "-o", str(decoded_path)], check=True)
    decoded = decoded_path.read_text(encoding="utf-8", errors="replace")
    for widget in ("Secondary TDM0 Playback", "DISPLAY_PORT_RX_0 Playback", "MultiMedia2 Playback", "MultiMedia3 Capture", "TX_CODEC_DMA_TX_3 Capture"):
        if widget not in decoded:
            raise RuntimeError(f"Compiled topology missing expected AudioReach widget: {widget}")


def build_audio(version: str, out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        stage = tmp_path / "pkg"
        stage.mkdir()

        ucm_src = REPO / "packaging/device-support/tree/usr/share/alsa"
        copy_tree_filtered(ucm_src, stage / "usr/share/alsa")
        ucm_conf_d = stage / "usr/share/alsa/ucm2/conf.d/sm8750"
        ucm_conf_d.mkdir(parents=True, exist_ok=True)
        ucm_link = ucm_conf_d / "Lenovo-TB378FC.conf"
        if not ucm_link.is_symlink():
            ucm_link.symlink_to("../../Qualcomm/sm8750/Lenovo-TB378FC/Lenovo-TB378FC.conf")

        audio_tree = REPO / "packaging/audio/tree"
        if audio_tree.is_dir():
            copy_tree_filtered(audio_tree, stage)

        user_wants = stage / "usr/lib/systemd/user/graphical-session.target.wants"
        user_wants.mkdir(parents=True, exist_ok=True)
        link = user_wants / "tb378fc-speaker-rotation.service"
        if not link.is_symlink():
            link.symlink_to("../tb378fc-speaker-rotation.service")

        tplg_src = stage / "usr/share/tb378fc/audio"
        tplg_src.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "packaging/audio/Lenovo-TB378FC.m4", tplg_src / "Lenovo-TB378FC.m4")
        shutil.copy2(REPO / "packaging/audio/source.json", tplg_src / "source.json")

        compile_audioreach_topology(
            tmp_path,
            stage / "lib/firmware/qcom/sm8750/Lenovo-TB378FC-tplg.bin",
        )

        write_control(
            stage,
            {
                "Package": "tb378fc-audio",
                "Version": version,
                "Architecture": "arm64",
                "Section": "sound",
                "Priority": "optional",
                "Maintainer": MAINTAINER,
                "Depends": "alsa-ucm-conf, python3, python3-gi",
                "Recommends": "pipewire-bin, wireplumber, libspa-0.2-bluetooth, libspa-0.2-modules-extra, libfdk-aac2",
                "Description": "Lenovo TB378FC AudioReach topology, ALSA UCM2, speaker rotation, and Bluetooth codecs\n"
                " Provides compiled Lenovo-TB378FC-tplg.bin, ALSA UCM2 profiles for quad\n"
                " AW88480 speakers + dual WCD9390 microphones, PipeWire speaker rotation,\n"
                " and WirePlumber Bluetooth A2DP (AAC/LDAC/aptX/SBC-XQ) & wideband HFP policy.",
            },
        )
        return pack_deb(stage, out_dir / f"tb378fc-audio_{version}_arm64.deb")


def build_kernel_deb(kernel_out: Path, version: str, out_dir: Path) -> tuple[Path, str]:
    result = json.loads((kernel_out / "build-result.json").read_text(encoding="utf-8"))
    krel = result["kernel_release"]
    pkg_name = "tb378fc-kernel-image"
    deb_ver = f"{krel.replace('_', '-')}-{version}"

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "pkg"
        stage.mkdir()

        boot_dir = stage / "boot"
        boot_dir.mkdir(parents=True)
        shutil.copy2(kernel_out / "arch/arm64/boot/Image", boot_dir / f"vmlinuz-{krel}")
        shutil.copy2(kernel_out / ".config", boot_dir / f"config-{krel}")
        dtb_dir = boot_dir / f"dtbs/{krel}/qcom"
        dtb_dir.mkdir(parents=True)
        shutil.copy2(
            kernel_out / "arch/arm64/boot/dts/qcom/sm8750-lenovo-tb378fc.dtb",
            dtb_dir / "sm8750-lenovo-tb378fc.dtb",
        )

        strip_bin = shutil.which("aarch64-linux-gnu-strip") or shutil.which("llvm-strip")
        staged_kos: list[Path] = []
        mod_root = stage / "lib/modules" / krel
        kernel_mod_dir = mod_root / "kernel"
        for rel_ko in sorted(result["modules"]):
            src_ko = kernel_out / rel_ko
            dst_ko = kernel_mod_dir / rel_ko
            dst_ko.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_ko, dst_ko)
            staged_kos.append(dst_ko)

        for rel_extra in sorted(result.get("extra_modules", {})):
            src_extra = kernel_out / "extra-modules" / Path(rel_extra).name
            if not src_extra.is_file():
                raise FileNotFoundError(f"Missing extra kernel module: {src_extra}")
            dst_extra = mod_root / rel_extra
            dst_extra.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_extra, dst_extra)
            staged_kos.append(dst_extra)

        # Strip bulky DWARF .debug_* sections while preserving compact .BTF & symbol tables
        if strip_bin and staged_kos:
            for i in range(0, len(staged_kos), 128):
                subprocess.run([strip_bin, "--strip-debug", *map(str, staged_kos[i : i + 128])], check=True)

        for meta in ("modules.order", "modules.builtin", "modules.builtin.modinfo"):
            src_m = kernel_out / meta
            if src_m.is_file():
                shutil.copy2(src_m, mod_root / meta)

        postinst = f"""#!/bin/sh
set -eu
if [ "$1" = "configure" ] && command -v depmod >/dev/null 2>&1; then
    depmod -a "{krel}" || true
fi
exit 0
"""
        write_control(
            stage,
            {
                "Package": pkg_name,
                "Version": deb_ver,
                "Architecture": "arm64",
                "Section": "kernel",
                "Priority": "optional",
                "Maintainer": MAINTAINER,
                "Depends": "kmod",
                "Description": f"Lenovo TB378FC unified mainline Linux kernel ({krel})\n"
                " Single unified SM8750P kernel image, board DTB, and standard\n"
                " /lib/modules tree for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC).",
            },
            postinst=postinst,
        )
        return pack_deb(stage, out_dir / f"{pkg_name}_{deb_ver}_arm64.deb"), pkg_name


def build_meta(version: str, deps: list[str], out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "pkg"
        stage.mkdir()
        write_control(
            stage,
            {
                "Package": "tb378fc-device",
                "Version": version,
                "Architecture": "arm64",
                "Section": "metapackages",
                "Priority": "optional",
                "Maintainer": MAINTAINER,
                "Depends": ", ".join(deps),
                "Recommends": "tb378fc-sensors, tb378fc-libcamera, tb378fc-mesa, tb378fc-turnip, tb378fc-qcom-va",
                "Description": "Lenovo Xiaoxin Pad Pro GT 13 (TB378FC) mainline device metapackage\n"
                " Installs the complete TB378FC device-support, audio, sensor, camera, and kernel packages.",
            },
        )
        return pack_deb(stage, out_dir / f"tb378fc-device_{version}_arm64.deb")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", default="0.2.0", help="Debian package version string")
    p.add_argument("--kernel-out", type=Path, help="Optional built kernel output directory containing build-result.json")
    p.add_argument("--output", type=Path, default=REPO / "out/debs", help="Output directory for .deb packages")
    a = p.parse_args()

    a.output.mkdir(parents=True, exist_ok=True)
    built = []
    deps = [f"tb378fc-device-support (= {a.version})", f"tb378fc-audio (= {a.version})"]

    built.append(build_device_support(a.version, a.output))
    built.append(build_audio(a.version, a.output))
    if a.kernel_out:
        kdeb, kpkg = build_kernel_deb(a.kernel_out, a.version, a.output)
        built.append(kdeb)
        deps.append(kpkg)
    built.append(build_meta(a.version, deps, a.output))

    manifest = {
        "device": "lenovo-tb378fc",
        "version": a.version,
        "packages": [
            {"file": b.name, "sha256": sha256(b), "bytes": b.stat().st_size}
            for b in built
        ],
    }
    (a.output / "debs-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for b in built:
        print(f"Built {b} ({b.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
