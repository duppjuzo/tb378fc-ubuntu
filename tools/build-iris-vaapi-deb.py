#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-compile Qualcomm Iris VA-API driver for TB378FC ARM64 (Ubuntu 26.04 resolute) and package .deb."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "packaging" / "iris-vaapi"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kwargs) -> None:
    print("+ " + " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kwargs)


def write_cross_file(cross_path: Path, pkgconfig_wrapper: Path, sysroot: Path, gcc: str, gxx: str) -> None:
    pkgconfig_wrapper.write_text(
        "#!/bin/sh\n"
        f"export PKG_CONFIG_SYSROOT_DIR={shlex.quote(str(sysroot))}\n"
        f"export PKG_CONFIG_LIBDIR={shlex.quote(f'{sysroot}/usr/lib/aarch64-linux-gnu/pkgconfig:{sysroot}/usr/share/pkgconfig')}\n"
        "unset PKG_CONFIG_PATH\n"
        'exec /usr/bin/pkg-config "$@"\n',
        encoding="utf-8",
    )
    pkgconfig_wrapper.chmod(0o755)
    c_args = [f"--sysroot={sysroot}"]
    link_args = [
        f"--sysroot={sysroot}",
        f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
    ]
    cross_path.write_text(
        f"""[binaries]
c = '{gcc}'
cpp = '{gxx}'
ar = 'aarch64-linux-gnu-ar'
strip = 'aarch64-linux-gnu-strip'
pkgconfig = '{pkgconfig_wrapper}'
pkg-config = '{pkgconfig_wrapper}'

[built-in options]
c_args = {repr(c_args)}
cpp_args = {repr(c_args)}
c_link_args = {repr(link_args)}
cpp_link_args = {repr(link_args)}

[properties]
sys_root = '{sysroot}'
pkg_config_libdir = '{sysroot}/usr/lib/aarch64-linux-gnu/pkgconfig:{sysroot}/usr/share/pkgconfig'
needs_exe_wrapper = true

[host_machine]
system = 'linux'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
""",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--meson", default="/root/tb378fc-work/cache/mesa-venv/bin/meson")
    parser.add_argument("--gcc", default="aarch64-linux-gnu-gcc-14")
    parser.add_argument("--gxx", default="aarch64-linux-gnu-g++-14")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    os.umask(0o022)
    lock = json.loads((INPUT / "source.json").read_text(encoding="utf-8"))
    archive = args.source_archive.resolve()
    if sha256(archive) != lock["archive_sha256"]:
        raise SystemExit(f"Source archive SHA-256 mismatch: {archive}")

    work = args.work_dir.resolve()
    out_dir = args.out_dir.resolve()
    sysroot = args.sysroot.resolve()
    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = work / f"qualcomm-iris-vaapi-{lock['commit']}"
    if not (src / ".tb378fc-patched").exists():
        if src.exists():
            shutil.rmtree(src)
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        for patch in lock["patches"]:
            run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(INPUT / patch)], cwd=src)
        (src / ".tb378fc-patched").write_text("ok\n", encoding="utf-8")

    cross_file = work / "aarch64-iris-vaapi.cross"
    pkgconfig_wrapper = work / "arm64-pkg-config.sh"
    write_cross_file(cross_file, pkgconfig_wrapper, sysroot, args.gcc, args.gxx)

    build = work / "build"
    if not (build / "build.ninja").exists():
        run([
            args.meson, "setup", str(build), str(src),
            "--cross-file", str(cross_file),
            "--prefix=/usr", "--libdir=lib/aarch64-linux-gnu", "--buildtype=release",
        ])

    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    if not any(c["file"].endswith("/iris/gpu_copy.cc") for c in commands):
        raise SystemExit("ERROR: iris/gpu_copy.cc was omitted — EGL/GLES/GBM headers missing in sysroot")
    if not any(c["file"].endswith("/iris/vpp.cc") for c in commands):
        raise SystemExit("ERROR: iris/vpp.cc was omitted — RGB-to-NV12 VPP patch not built")

    run([args.meson, "compile", "-C", str(build), "-j", str(args.jobs)])

    driver = build / "src/v4l2_drv_video.so"
    elf = subprocess.check_output(["readelf", "-h", str(driver)], text=True)
    if "AArch64" not in elf:
        raise SystemExit(f"Built driver is not AArch64: {driver}")

    pkg = "tb378fc-iris-vaapi"
    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    libdir = stage / "usr/lib/aarch64-linux-gnu/dri"
    libdir.mkdir(parents=True)
    shutil.copy2(driver, libdir / driver.name)
    # Native libva discovery via MSM DRM driver name (no LIBVA_DRIVER_NAME hack needed)
    (libdir / "msm_drv_video.so").symlink_to(driver.name)

    doc = stage / "usr/share/doc" / pkg
    doc.mkdir(parents=True)
    for name in ("COPYING", "COPYING.MIT", "COPYING.LGPL", "AUTHORS", "CREDITS", "source.json", "README.md"):
        shutil.copy2(INPUT / name, doc / name)
    shutil.copy2(INPUT / "COPYING", doc / "copyright")

    with tarfile.open(archive) as orig:
        epoch = min(m.mtime for m in orig.getmembers())

    control = stage / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(
        f"Package: {pkg}\n"
        f"Version: {lock['version']}\n"
        f"Architecture: arm64\n"
        f"Maintainer: TB378FC Maintainer <maintainer@tb378fc.local>\n"
        f"Section: video\n"
        f"Priority: optional\n"
        f"Depends: libc6 (>= 2.38), libstdc++6 (>= 13.1), libgcc-s1, libva2 (>= 2.23), libdrm2 (>= 2.4.52), libegl1, libgles2, libgbm1\n"
        f"Conflicts: libva-v4l2, libva-v4l2-iris, erhai-iris-vaapi\n"
        f"Description: Qualcomm Iris VA-API video driver for Lenovo TB378FC\n"
        f" Stateful V4L2 H.264/HEVC/VP9 decode/encode and RGB-to-NV12 VPP via Iris VPU35.\n",
        encoding="utf-8",
    )

    deb_path = out_dir / f"{pkg}_{lock['version']}_arm64.deb"
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(int(epoch)))
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(deb_path)], env=env)
    manifest = {
        "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
        "deb": deb_path.name,
        "sha256": sha256(deb_path),
        "driver_sha256": sha256(driver),
        "version": lock["version"],
        "commit": lock["commit"],
        "gpu_copy_enabled": True,
        "vpp_enabled": True,
    }
    (out_dir / "tb378fc-iris-vaapi-build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
