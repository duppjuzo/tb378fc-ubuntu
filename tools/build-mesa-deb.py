#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Cross-compile Mesa 26.2.6 (Freedreno + Turnip) for TB378FC ARM64 with GCC 14 and package a .deb."""
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
MESA_DIR = REPO / "packaging" / "mesa"
DEFAULT_PREFIX = "/usr/lib/tb378fc/mesa"
DEFAULT_DEB_REV = "0.2.2"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], **kwargs) -> None:
    print("+ " + " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kwargs)


def write_cross_file(
    cross_path: Path,
    pkgconfig_wrapper: Path,
    sysroot: Path,
    src_dir: Path,
    prefix: str,
    gcc: str,
    gxx: str,
) -> None:
    pkgconfig_wrapper.write_text(
        "#!/bin/sh\n"
        f"export PKG_CONFIG_SYSROOT_DIR={shlex.quote(str(sysroot))}\n"
        f"export PKG_CONFIG_LIBDIR={shlex.quote(f'{sysroot}/usr/lib/aarch64-linux-gnu/pkgconfig:{sysroot}/usr/share/pkgconfig')}\n"
        "unset PKG_CONFIG_PATH\n"
        'exec /usr/bin/pkg-config "$@"\n',
        encoding="utf-8",
    )
    pkgconfig_wrapper.chmod(0o755)

    c_args = [
        f"--sysroot={sysroot}",
        f"-ffile-prefix-map={src_dir}=/mesa",
    ]
    link_args = [
        f"--sysroot={sysroot}",
        f"-Wl,-rpath,{prefix}/lib",
        f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
    ]
    cross_text = f"""[binaries]
c = '{gcc}'
cpp = '{gxx}'
ar = 'aarch64-linux-gnu-ar'
strip = 'aarch64-linux-gnu-strip'
pkgconfig = '{pkgconfig_wrapper}'
pkg-config = '{pkgconfig_wrapper}'
wayland-scanner = '/usr/bin/wayland-scanner'

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
"""
    cross_path.write_text(cross_text, encoding="utf-8")


def verify_elfs(stage_prefix: Path, prefix: str) -> dict:
    so_files = sorted(
        p for p in stage_prefix.rglob("*.so*")
        if p.is_file() and not p.is_symlink()
    )
    if not so_files:
        raise SystemExit(f"No shared libraries staged under {stage_prefix}")
    verified = {}
    for so in so_files:
        f_out = subprocess.check_output(["file", "-b", str(so)], text=True).strip()
        if "ARM aarch64" not in f_out:
            raise SystemExit(f"Non-ARM64 ELF staged: {so}: {f_out}")
        dyn = subprocess.check_output(["readelf", "-d", str(so)], text=True)
        needed = []
        rpaths = []
        for line in dyn.splitlines():
            if "(NEEDED)" in line and "[" in line:
                needed.append(line.split("[", 1)[1].split("]", 1)[0])
            elif ("(RUNPATH)" in line or "(RPATH)" in line) and "[" in line:
                rpaths.append(line.split("[", 1)[1].split("]", 1)[0])
        verified[str(so.relative_to(stage_prefix))] = {
            "size": so.stat().st_size,
            "sha256": sha256(so),
            "file": f_out,
            "needed": needed,
            "rpath": rpaths,
        }
    icd_path = stage_prefix / "share/vulkan/icd.d/freedreno_icd.aarch64.json"
    if not icd_path.is_file():
        raise SystemExit(f"Missing Turnip ICD manifest: {icd_path}")
    icd = json.loads(icd_path.read_text(encoding="utf-8"))
    lib_path = icd.get("ICD", {}).get("library_path", "")
    if prefix not in lib_path:
        raise SystemExit(f"Unexpected Turnip ICD library_path: {lib_path!r}")
    egl_vendor = stage_prefix / "share/glvnd/egl_vendor.d/50_mesa.json"
    if not egl_vendor.is_file():
        raise SystemExit(f"Missing GLVND EGL vendor manifest: {egl_vendor}")
    return {
        "shared_libraries": verified,
        "vulkan_icd": icd,
        "egl_vendor": json.loads(egl_vendor.read_text(encoding="utf-8")),
    }


def package_deb(
    stage_root: Path,
    out_dir: Path,
    lock: dict,
    deb_rev: str,
    provenance: dict,
    epoch: str,
) -> Path:
    version = f"{lock['version']}-{deb_rev}"
    pkg_dir = out_dir / "deb-stage"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    shutil.copytree(stage_root, pkg_dir, symlinks=True)

    # Compatibility symlink for legacy tb378fc-vulkan path
    turnip_link = pkg_dir / "usr/lib/tb378fc/turnip"
    if not turnip_link.exists() and not turnip_link.is_symlink():
        turnip_link.symlink_to("mesa")

    doc_dir = pkg_dir / "usr/share/doc/tb378fc-mesa"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "build.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    (doc_dir / "copyright").write_text(
        "Mesa 3D Graphics Library (Freedreno + Turnip for Lenovo TB378FC)\n"
        "License: MIT\n"
        "Packaging scripts: GPL-2.0-only\n",
        encoding="utf-8",
    )

    debian = pkg_dir / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    installed_kb = sum(
        p.stat().st_size for p in pkg_dir.rglob("*") if p.is_file() and not p.is_symlink()
    ) // 1024
    control = (
        f"Package: tb378fc-mesa\n"
        f"Version: {version}\n"
        f"Architecture: arm64\n"
        f"Maintainer: TB378FC Maintainer <maintainer@tb378fc.local>\n"
        f"Installed-Size: {installed_kb}\n"
        f"Section: graphics\n"
        f"Priority: optional\n"
        f"Depends: libc6 (>= 2.38), libdrm2 (>= 2.4.120), libelf1t64 | libelf1, "
        f"libexpat1, libglvnd0, libstdc++6 (>= 14), libwayland-client0, libwayland-server0, "
        f"libx11-6, libx11-xcb1, libxcb1, libxcb-dri3-0, libxcb-present0, libxcb-randr0, "
        f"libxcb-shm0, libxcb-sync1, libxcb-xfixes0, libxext6, libxfixes3, libxshmfence1, "
        f"libxxf86vm1, libzstd1, zlib1g\n"
        f"Description: Isolated Mesa {lock['version']} (Freedreno + Turnip) for Lenovo TB378FC\n"
        f" Built with GCC 14 at -O2 against Ubuntu 24.04 ARM64 sysroot into\n"
        f" /usr/lib/tb378fc/mesa with Adreno 830v1 and thread-safe texture seqno fixes.\n"
    )
    (debian / "control").write_text(control, encoding="utf-8")

    deb_path = out_dir / f"tb378fc-mesa_{version}_arm64.deb"
    env = dict(
        os.environ,
        SOURCE_DATE_EPOCH=epoch,
        DPKG_DEB_THREADS_MAX=str(os.cpu_count() or 1),
    )
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(pkg_dir), str(deb_path)], env=env)
    shutil.rmtree(pkg_dir)
    return deb_path


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
    parser.add_argument("--deb-rev", default=DEFAULT_DEB_REV)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    lock = json.loads((MESA_DIR / "source.json").read_text(encoding="utf-8"))
    archive = args.source_archive.resolve()
    if sha256(archive) != lock["archive_sha256"]:
        raise SystemExit(f"Archive SHA-256 mismatch for {archive}")

    patches = [
        {"path": lock["patch"], "sha256": lock["patch_sha256"]},
        *lock.get("additional_patches", []),
    ]
    for p in patches:
        p_path = MESA_DIR / p["path"]
        if not p_path.is_file():
            raise SystemExit(f"Missing Mesa patch: {p_path}")
        if sha256(p_path) != p["sha256"]:
            raise SystemExit(f"SHA-256 mismatch for Mesa patch: {p_path}")

    gcc_ver = subprocess.check_output([args.gcc, "--version"], text=True).splitlines()[0]
    gxx_ver = subprocess.check_output([args.gxx, "--version"], text=True).splitlines()[0]
    if " 14." not in gcc_ver:
        raise SystemExit(f"Expected GCC 14 cross compiler (avoid GCC 13 GLSL bug), got: {gcc_ver}")

    work = args.work_dir.resolve()
    out_dir = args.out_dir.resolve()
    sysroot = args.sysroot.resolve()
    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    src = work / f"mesa-{lock['version']}"
    epoch_file = work / "source-date-epoch"
    if not (src / ".tb378fc-patched").exists():
        if src.exists():
            shutil.rmtree(src)
        with tarfile.open(archive) as tar:
            epoch = str(int(tar.getmember(f"mesa-{lock['version']}/VERSION").mtime))
            tar.extractall(work, filter="data")
        for p in patches:
            run(
                ["patch", "-p1", "--batch", "--forward", "--fuzz=0", "-i", str(MESA_DIR / p["path"])],
                cwd=src,
            )
        (src / ".tb378fc-patched").write_text("ok\n", encoding="utf-8")
        epoch_file.write_text(epoch, encoding="utf-8")
    epoch = epoch_file.read_text(encoding="utf-8").strip()

    prefix = lock.get("prefix", DEFAULT_PREFIX)
    cross_file = work / "aarch64-tb378fc.cross"
    pkgconfig_wrapper = work / "arm64-pkg-config.sh"
    write_cross_file(cross_file, pkgconfig_wrapper, sysroot, src, prefix, args.gcc, args.gxx)

    flags = [
        f"--prefix={prefix}",
        "--libdir=lib",
        "--buildtype=release",
        "--optimization=2",
        "--wrap-mode=nofallback",
        "-Db_ndebug=true",
        "-Db_lto=false",
        "-Dgallium-drivers=freedreno,softpipe",
        "-Dvulkan-drivers=freedreno",
        "-Dplatforms=wayland,x11",
        "-Degl=enabled",
        "-Dgbm=enabled",
        "-Dgles1=disabled",
        "-Dgles2=enabled",
        "-Dglx=dri",
        "-Dglvnd=enabled",
        "-Dllvm=disabled",
        "-Dgallium-va=disabled",
        "-Dvideo-codecs=[]",
        "-Dspirv-tools=disabled",
        "-Dvalgrind=disabled",
        "-Dbuild-tests=false",
        "-Dfreedreno-kmds=msm",
        "-Dlibunwind=disabled",
        "-Dlmsensors=disabled",
    ]
    env = dict(
        os.environ,
        SOURCE_DATE_EPOCH=epoch,
        PYTHONHASHSEED="0",
    )
    build = work / "build"
    if not (build / "build.ninja").exists():
        run([args.meson, "setup", str(build), str(src), *flags, "--cross-file", str(cross_file)], env=env)

    # Verify target architecture, -O2, -DNDEBUG, and GCC 14 via meson introspect
    machines = json.loads(subprocess.check_output([args.meson, "introspect", "--machines", str(build)], text=True))
    if machines["host"]["cpu_family"] != "aarch64":
        raise SystemExit(f"Expected aarch64 host_machine, got {machines['host']}")
    options = {
        opt["name"]: opt["value"]
        for opt in json.loads(
            subprocess.check_output([args.meson, "introspect", "--buildoptions", str(build)], text=True)
        )
    }
    if options["optimization"] != "2":
        raise SystemExit(f"Expected optimization=2, got {options['optimization']}")
    if options["b_ndebug"] != "true":
        raise SystemExit(f"Expected b_ndebug=true, got {options['b_ndebug']}")

    run([args.meson, "compile", "-C", str(build), "-j", str(args.jobs)], env=env)

    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    run([args.meson, "install", "-C", str(build), "--no-rebuild", "--destdir", str(stage)], env=env)

    stage_prefix = stage / prefix.lstrip("/")
    elf_report = verify_elfs(stage_prefix, prefix)

    provenance = {
        "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
        "mesa_version": lock["version"],
        "archive_sha256": lock["archive_sha256"],
        "patches": patches,
        "prefix": prefix,
        "meson_version": subprocess.check_output([args.meson, "--version"], text=True).strip(),
        "compilers": {"c": gcc_ver, "cpp": gxx_ver},
        "flags": flags,
        "source_date_epoch": epoch,
        "elf_verification": elf_report,
    }
    deb_path = package_deb(stage, out_dir, lock, args.deb_rev, provenance, epoch)
    manifest = {
        "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
        "deb": deb_path.name,
        "sha256": sha256(deb_path),
        "size_bytes": deb_path.stat().st_size,
        "mesa_version": lock["version"],
        "compilers": provenance["compilers"],
        "shared_libraries": list(elf_report["shared_libraries"].keys()),
        "vulkan_icd": elf_report["vulkan_icd"],
        "egl_vendor": elf_report["egl_vendor"],
    }
    (out_dir / "tb378fc-mesa-build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
