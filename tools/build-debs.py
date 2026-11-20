#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Build rootless ARM64 Debian packages for Lenovo TB378FC.

Produces:
  - tb378fc-device-support_<ver>_arm64.deb  (systemd units, helpers, udev rules, symlinks)
  - tb378fc-audio_<ver>_arm64.deb           (ALSA UCM2 + AudioReach topology source)
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
        ["dpkg-deb", "--root-owner-group", "--build", str(stage), str(out_deb)],
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
        # Audio UCM2 goes into tb378fc-audio so packages stay cleanly separated
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
            'if [ "$1" = "configure" ] && [ -d /run/systemd/system ]; then\n'
            "    systemctl daemon-reload || true\n"
            "    udevadm control --reload-rules || true\n"
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


def build_audio(version: str, out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "pkg"
        stage.mkdir()
        ucm_src = REPO / "packaging/device-support/tree/usr/share/alsa"
        if ucm_src.is_dir():
            copy_tree_filtered(ucm_src, stage / "usr/share/alsa")
        m4_src = REPO / "packaging/audio/Lenovo-TB378FC.m4"
        if m4_src.is_file():
            dst = stage / "usr/share/tb378fc/audio/Lenovo-TB378FC.m4"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(m4_src, dst)

        write_control(
            stage,
            {
                "Package": "tb378fc-audio",
                "Version": version,
                "Architecture": "arm64",
                "Section": "sound",
                "Priority": "optional",
                "Maintainer": MAINTAINER,
                "Depends": "alsa-ucm-conf",
                "Description": "Lenovo TB378FC ALSA UCM2 profiles and AudioReach topology source\n"
                " Provides ALSA Use Case Manager configuration for quad AW88480 speakers\n"
                " and WCD9390 SoundWire microphones on Lenovo TB378FC.",
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

        mod_root = stage / "lib/modules" / krel
        kernel_mod_dir = mod_root / "kernel"
        for rel_ko in sorted(result["modules"]):
            src_ko = kernel_out / rel_ko
            dst_ko = kernel_mod_dir / rel_ko
            dst_ko.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_ko, dst_ko)

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
                "Description": "Lenovo Xiaoxin Pad Pro GT 13 (TB378FC) mainline device metapackage\n"
                " Installs the complete TB378FC device-support, audio, and kernel packages.",
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
