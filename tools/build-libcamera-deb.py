#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Cross-compile TB378FC patched libcamera 0.7.2 (EGL debayer, CDAF, OV13B10/GC08A8 tuning) for Ubuntu 26.04 ARM64."""
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
        f"-L{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-L{sysroot}/lib/aarch64-linux-gnu",
        f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-Wl,-rpath-link,{sysroot}/lib/aarch64-linux-gnu",
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
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--meson", default="/root/tb378fc-work/cache/mesa-venv/bin/meson")
    parser.add_argument("--gcc", default="aarch64-linux-gnu-gcc-14")
    parser.add_argument("--gxx", default="aarch64-linux-gnu-g++-14")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--deb-rev", default="0.2.0")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    os.umask(0o022)
    src = args.source_dir.resolve()
    sysroot = args.sysroot.resolve()
    work = args.work_dir.resolve()
    out_dir = args.out_dir.resolve()
    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    cross_file = work / "aarch64-libcamera.cross"
    pkgconfig_wrapper = work / "arm64-pkg-config.sh"
    write_cross_file(cross_file, pkgconfig_wrapper, sysroot, args.gcc, args.gxx)

    build = work / "build"
    flags = [
        "--cross-file", str(cross_file),
        "--prefix=/usr",
        "--libdir=lib/aarch64-linux-gnu",
        "--buildtype=release",
        "-Dwerror=false",
        "-Dpipelines=simple",
        "-Dipas=simple",
        "-Dgstreamer=enabled",
        "-Dv4l2=enabled",
        "-Dcam=enabled",
        "-Dqcam=disabled",
        "-Dlc-compliance=disabled",
        "-Dpycamera=disabled",
        "-Ddocumentation=disabled",
        "-Dtest=false",
    ]
    if not (build / "build.ninja").exists():
        run([args.meson, "setup", str(build), str(src), *flags])

    run([args.meson, "compile", "-C", str(build), "-j", str(args.jobs)])

    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    run([args.meson, "install", "-C", str(build), "--no-rebuild", "--destdir", str(stage)])

    # Verify ARM64 ELF + SONAME 0.7 + signed soft ISP IPA + GStreamer plugin + v4l2 compat
    libcam = stage / "usr/lib/aarch64-linux-gnu/libcamera.so.0.7"
    if not libcam.exists():
        raise SystemExit(f"Missing expected libcamera 0.7 SONAME: {libcam}")
    f_out = subprocess.check_output(["file", "-bL", str(libcam)], text=True).strip()
    if "ARM aarch64" not in f_out:
        raise SystemExit(f"Non-ARM64 libcamera built: {f_out}")
    ipa_so = stage / "usr/lib/aarch64-linux-gnu/libcamera/ipa/ipa_soft_simple.so"
    ipa_sig = stage / "usr/lib/aarch64-linux-gnu/libcamera/ipa/ipa_soft_simple.so.sign"
    gst_so = stage / "usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstlibcamera.so"
    v4l2_so = stage / "usr/libexec/libcamera/v4l2-compat.so"
    proxy_bin = stage / "usr/libexec/libcamera/soft_ipa_proxy"
    ov_yaml = stage / "usr/share/libcamera/ipa/simple/ov13b10.yaml"
    for req in (ipa_so, ipa_sig, gst_so, v4l2_so, proxy_bin, ov_yaml):
        if not req.is_file():
            raise SystemExit(f"Missing staged libcamera component: {req}")

    proj = json.loads(subprocess.check_output([args.meson, "introspect", "--projectinfo", str(build)], text=True))
    version = proj["version"]
    commit = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()

    doc = stage / "usr/share/doc/tb378fc-libcamera"
    doc.mkdir(parents=True, exist_ok=True)
    for lic in ("COPYING.rst", "LICENSES/LGPL-2.1-or-later.txt", "LICENSES/GPL-2.0-or-later.txt"):
        lp = src / lic
        if lp.is_file():
            shutil.copy2(lp, doc / lp.name)
    (doc / "source.json").write_text(
        json.dumps({
            "repository": "https://github.com/PickaxeHit/libcamera-zhiyu",
            "branch": "zhiyu-0.7.2",
            "commit": commit,
            "version": version,
            "license": "LGPL-2.1-or-later AND GPL-2.0-or-later",
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    # Remove dev headers/pkgconfig to keep runtime package lean and avoid conflicts with libcamera-dev
    shutil.rmtree(stage / "usr/include", ignore_errors=True)
    shutil.rmtree(stage / "usr/lib/aarch64-linux-gnu/pkgconfig", ignore_errors=True)

    pkg_ver = f"{version}-{args.deb_rev}"
    debian = stage / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    installed_kb = sum(
        p.stat().st_size for p in stage.rglob("*") if p.is_file() and not p.is_symlink()
    ) // 1024
    (debian / "control").write_text(
        f"Package: tb378fc-libcamera\n"
        f"Version: {pkg_ver}\n"
        f"Architecture: arm64\n"
        f"Maintainer: TB378FC Maintainer <maintainer@tb378fc.local>\n"
        f"Installed-Size: {installed_kb}\n"
        f"Section: video\n"
        f"Priority: optional\n"
        f"Depends: libc6 (>= 2.38), libstdc++6 (>= 14), libgcc-s1, libudev1, libyaml-0-2, "
        f"libgnutls30t64 | libgnutls30, libdrm2, libegl1, libgles2, libgbm1, "
        f"libevent-2.1-7t64 | libevent-2.1-7, libevent-pthreads-2.1-7t64 | libevent-pthreads-2.1-7, "
        f"libjpeg-turbo8 | libjpeg8, libgstreamer1.0-0, libgstreamer-plugins-base1.0-0, libglib2.0-0t64 | libglib2.0-0\n"
        f"Provides: libcamera0.7 (= {pkg_ver}), libcamera-ipa (= {pkg_ver}), libcamera-tools (= {pkg_ver}), gstreamer1.0-libcamera (= {pkg_ver})\n"
        f"Conflicts: libcamera0.7, libcamera-ipa, libcamera-tools, gstreamer1.0-libcamera, libcamera-zhiyu\n"
        f"Replaces: libcamera0.7, libcamera-ipa, libcamera-tools, gstreamer1.0-libcamera, libcamera-zhiyu\n"
        f"Description: TB378FC patched libcamera {version} (simple pipeline, EGL debayer, CDAF)\n"
        f" Drop-in libcamera 0.7 ABI stack for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)\n"
        f" with GC08A8/OV13B10 tuning, DW9807 contrast-detect autofocus, and Adreno EGL debayer.\n",
        encoding="utf-8",
    )

    deb_path = out_dir / f"tb378fc-libcamera_{pkg_ver}_arm64.deb"
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(deb_path)])
    manifest = {
        "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
        "deb": deb_path.name,
        "sha256": sha256(deb_path),
        "size_bytes": deb_path.stat().st_size,
        "version": pkg_ver,
        "commit": commit,
        "soname": "libcamera.so.0.7",
        "ipa_signed": True,
        "gstreamer_plugin": True,
        "v4l2_compat": True,
    }
    (out_dir / "tb378fc-libcamera-build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
