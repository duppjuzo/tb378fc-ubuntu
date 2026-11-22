#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Cross-compile and package TB378FC Qualcomm ADSP sensor & magnetic folio stack (tb378fc-sensors) for Ubuntu 26.04 ARM64.

Builds:
  - hexagonrpcd (linux-msm/hexagonrpc) + systemd RootPD/SensorsPD units
  - libqrtr     (linux-msm/qrtr, private RPATH under /usr/lib/tb378fc/sensors/lib)
  - sensh       (flamingradian/sensh, SNS Client QMI/protobuf tool)
  - tb378fc-sensord (net.hadess.SensorProxy auto-rotation + SW_LID magnetic cover daemon)
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
MAINTAINER = "TB378FC Maintainers <tb378fc@localhost>"
PRIV_LIBDIR = "/usr/lib/tb378fc/sensors/lib"


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
    gcc: str,
    extra_inc: list[Path] = (),
    extra_lib: list[Path] = (),
    rpath: str = "",
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
    c_args = [f"--sysroot={sysroot}", *[f"-I{inc}" for inc in extra_inc]]
    link_args = [
        f"--sysroot={sysroot}",
        *[f"-L{lib}" for lib in extra_lib],
        f"-L{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-L{sysroot}/lib/aarch64-linux-gnu",
        *[f"-Wl,-rpath-link,{lib}" for lib in extra_lib],
        f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-Wl,-rpath-link,{sysroot}/lib/aarch64-linux-gnu",
    ]
    if rpath:
        link_args.append(f"-Wl,-rpath,{rpath}")

    cross_path.write_text(
        f"""[binaries]
c = '{gcc}'
ar = 'aarch64-linux-gnu-ar'
strip = 'aarch64-linux-gnu-strip'
pkgconfig = '{pkgconfig_wrapper}'
pkg-config = '{pkgconfig_wrapper}'

[built-in options]
c_args = {repr(c_args)}
c_link_args = {repr(link_args)}

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


def ensure_sources(src_root: Path, lock: dict) -> dict[str, str]:
    src_root.mkdir(parents=True, exist_ok=True)
    revs = {}
    for comp in lock["components"]:
        if "url" not in comp:
            continue
        name = comp["name"]
        dest = src_root / name
        if not (dest / ".git").is_dir():
            run(["git", "clone", comp["url"], str(dest)])
        if comp.get("commit"):
            run(["git", "-C", str(dest), "checkout", "-f", comp["commit"]])
        revs[name] = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    return revs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-dir", type=Path, default=Path("/root/tb378fc-work/cache/sensors-src"))
    parser.add_argument("--sysroot", type=Path, default=Path("/root/tb378fc-work/cache/arm64-resolute-sysroot"))
    parser.add_argument("--work-dir", type=Path, default=Path("/root/tb378fc-work/out/sensors-build"))
    parser.add_argument("--out-dir", type=Path, default=REPO / "out/debs")
    parser.add_argument("--meson", default="/root/tb378fc-work/cache/mesa-venv/bin/meson")
    parser.add_argument("--gcc", default="aarch64-linux-gnu-gcc-14")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--version", default="0.2.0")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    os.umask(0o022)
    lock = json.loads((REPO / "packaging/sensors/source.json").read_text(encoding="utf-8"))
    sysroot = args.sysroot.resolve()
    work = args.work_dir.resolve()
    out_dir = args.out_dir.resolve()
    meson_bin = args.meson if Path(args.meson).is_file() else "meson"

    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    revs = ensure_sources(args.sources_dir.resolve(), lock)
    sroot = args.sources_dir.resolve()

    # Keep hexagonrpcd alive when ADSP probes optional unsupported apps_std methods (e.g. method 36)
    lc = sroot / "hexagonrpc/hexagonrpcd/listener.c"
    lc_txt = lc.read_text(encoding="utf-8")
    lc_txt = lc_txt.replace(
        '*result = AEE_EUNSUPPORTED;\n\t\treturn 1;',
        '*returned = NULL;\n\t\t*result = AEE_EUNSUPPORTED;\n\t\treturn 0;',
    )
    lc_txt = lc_txt.replace(
        'n_outbufs = REMOTE_SCALARS_OUTBUFS(sc);',
        'n_outbufs = returned != NULL ? REMOTE_SCALARS_OUTBUFS(sc) : 0;',
    )
    lc.write_text(lc_txt, encoding="utf-8")

    # 1. Build native host qmic code generator
    host_bin = work / "host-bin"
    host_bin.mkdir(parents=True, exist_ok=True)
    qmic_work = work / "qmic-host"
    if qmic_work.exists():
        shutil.rmtree(qmic_work)
    shutil.copytree(sroot / "qmic", qmic_work)
    run(["make", "-C", str(qmic_work), "CC=gcc", f"-j{args.jobs}"])
    shutil.copy2(qmic_work / "qmic", host_bin / "qmic")
    (host_bin / "qmic").chmod(0o755)

    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # 2. Cross-compile hexagonrpc (--libdir=lib so systemd units land in /usr/lib/systemd/system)
    hrpc_cross = work / "aarch64-hrpc.cross"
    pkg_wrap = work / "arm64-pkg-config.sh"
    write_cross_file(hrpc_cross, pkg_wrap, sysroot, args.gcc, rpath=PRIV_LIBDIR)
    hrpc_build = work / "build-hexagonrpc"
    if hrpc_build.exists():
        shutil.rmtree(hrpc_build)
    run([
        meson_bin, "setup", str(hrpc_build), str(sroot / "hexagonrpc"),
        "--cross-file", str(hrpc_cross),
        "--prefix=/usr",
        "--libdir=lib",
        "--buildtype=release",
    ])
    run([meson_bin, "compile", "-C", str(hrpc_build), "-j", str(args.jobs)])
    hrpc_stage = work / "stage-hrpc"
    if hrpc_stage.exists():
        shutil.rmtree(hrpc_stage)
    run([meson_bin, "install", "-C", str(hrpc_build), "--no-rebuild", "--destdir", str(hrpc_stage)])

    # Stage hexagonrpcd, private libhexagonrpc, dummy DSP libs, and ADSP systemd units
    priv_lib = stage / PRIV_LIBDIR.lstrip("/")
    priv_lib.mkdir(parents=True, exist_ok=True)
    (stage / "usr/bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(hrpc_stage / "usr/bin/hexagonrpcd", stage / "usr/bin/hexagonrpcd")
    for so in (hrpc_stage / "usr/lib").glob("libhexagonrpc.so*"):
        if so.is_symlink():
            (priv_lib / so.name).symlink_to(os.readlink(so))
        else:
            shutil.copy2(so, priv_lib / so.name)
    if (hrpc_stage / "usr/libexec/hexagonrpc").is_dir():
        shutil.copytree(hrpc_stage / "usr/libexec/hexagonrpc", stage / "usr/libexec/hexagonrpc")
    if (hrpc_stage / "usr/share/qcom").is_dir():
        shutil.copytree(hrpc_stage / "usr/share/qcom", stage / "usr/share/qcom", dirs_exist_ok=True)

    unit_dir = stage / "usr/lib/systemd/system"
    unit_dir.mkdir(parents=True, exist_ok=True)
    for unit_name in ("hexagonrpcd-adsp-rootpd.service", "hexagonrpcd-adsp-sensorspd.service"):
        shutil.copy2(hrpc_stage / "usr/lib/systemd/system" / unit_name, unit_dir / unit_name)

    # 3. Cross-compile qrtr into private libdir (/usr/lib/tb378fc/sensors/lib)
    qrtr_stage = work / "stage-qrtr"
    if qrtr_stage.exists():
        shutil.rmtree(qrtr_stage)
    qrtr_build = work / "build-qrtr"
    if qrtr_build.exists():
        shutil.rmtree(qrtr_build)
    run([
        meson_bin, "setup", str(qrtr_build), str(sroot / "qrtr"),
        "--cross-file", str(hrpc_cross),
        "--prefix=/usr",
        "--libdir=lib",
        "--buildtype=release",
    ])
    run([meson_bin, "compile", "-C", str(qrtr_build), "-j", str(args.jobs)])
    run([meson_bin, "install", "-C", str(qrtr_build), "--no-rebuild", "--destdir", str(qrtr_stage)])
    for so in (qrtr_stage / "usr/lib").glob("libqrtr.so*"):
        if so.is_symlink():
            (priv_lib / so.name).symlink_to(os.readlink(so))
        else:
            shutil.copy2(so, priv_lib / so.name)

    # Also bundle libprotobuf-c.so.1* in private libdir so offline Ubuntu ISO installs need zero extra debs
    for so in (sysroot / "usr/lib/aarch64-linux-gnu").glob("libprotobuf-c.so.1*"):
        if so.is_symlink():
            (priv_lib / so.name).symlink_to(os.readlink(so))
        else:
            shutil.copy2(so, priv_lib / so.name)

    # 4. Cross-compile sensh with host qmic + protoc-c and private RPATH
    sensh_cross = work / "aarch64-sensh.cross"
    write_cross_file(
        sensh_cross,
        pkg_wrap,
        sysroot,
        args.gcc,
        extra_inc=[qrtr_stage / "usr/include"],
        extra_lib=[qrtr_stage / "usr/lib"],
        rpath=PRIV_LIBDIR,
    )
    sensh_build = work / "build-sensh"
    if sensh_build.exists():
        shutil.rmtree(sensh_build)
    env = os.environ.copy()
    env["PATH"] = f"{host_bin}:{env.get('PATH', '')}"
    run([
        meson_bin, "setup", str(sensh_build), str(sroot / "sensh"),
        "--cross-file", str(sensh_cross),
        "--prefix=/usr",
        "--buildtype=release",
    ], env=env)
    run([meson_bin, "compile", "-C", str(sensh_build), "-j", str(args.jobs)], env=env)
    shutil.copy2(sensh_build / "sensh", stage / "usr/bin/sensh")
    (stage / "usr/bin/sensh").chmod(0o755)

    # 5. Cross-compile tb378fc-sensord
    (stage / "usr/sbin").mkdir(parents=True, exist_ok=True)
    glib_cflags = subprocess.check_output([str(pkg_wrap), "--cflags", "glib-2.0", "gio-2.0"], text=True).split()
    glib_libs = subprocess.check_output([str(pkg_wrap), "--libs", "glib-2.0", "gio-2.0"], text=True).split()
    sensord_bin = stage / "usr/sbin/tb378fc-sensord"
    run([
        args.gcc,
        f"--sysroot={sysroot}",
        "-O2", "-Wall", "-Wextra",
        '-DSENSH="/usr/bin/sensh"',
        *glib_cflags,
        str(REPO / "packaging/sensors/tb378fc-sensord.c"),
        "-o", str(sensord_bin),
        f"-L{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-L{sysroot}/lib/aarch64-linux-gnu",
        f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
        f"-Wl,-rpath-link,{sysroot}/lib/aarch64-linux-gnu",
        *glib_libs,
        "-lm",
    ])
    sensord_bin.chmod(0o755)

    # Verify all built ELFs are ARM64
    for elf in (stage / "usr/bin/hexagonrpcd", stage / "usr/bin/sensh", sensord_bin):
        fout = subprocess.check_output(["file", "-bL", str(elf)], text=True).strip()
        if "ARM aarch64" not in fout:
            raise SystemExit(f"Non-ARM64 ELF produced for {elf}: {fout}")

    # 6. Install systemd units, drop-ins, udev, modules-load, sysusers, tmpfiles, and D-Bus policy
    for drop_unit in ("hexagonrpcd-adsp-rootpd.service", "hexagonrpcd-adsp-sensorspd.service"):
        ddir = unit_dir / f"{drop_unit}.d"
        ddir.mkdir(parents=True, exist_ok=True)
        extra_cond = "ConditionPathExists=/usr/share/qcom/sensors/sns_reg.conf\n" if "sensorspd" in drop_unit else ""
        (ddir / "10-tb378fc.conf").write_text(
            "[Unit]\n"
            f"{extra_cond}"
            "[Service]\n"
            "ExecStartPre=-/sbin/modprobe -q fastrpc\n",
            encoding="utf-8",
        )

    (unit_dir / "tb378fc-sensord.service").write_text(
        "[Unit]\n"
        "Description=TB378FC SensorProxy & Magnetic Folio Lid daemon (sensh/SNS Client)\n"
        "ConditionPathExists=/usr/share/qcom/sensors/sns_reg.conf\n"
        "After=hexagonrpcd-adsp-sensorspd.service\n"
        "Wants=hexagonrpcd-adsp-sensorspd.service\n"
        "Conflicts=iio-sensor-proxy.service\n"
        "Before=iio-sensor-proxy.service upower.service display-manager.service\n\n"
        "[Service]\n"
        "Type=notify\n"
        "NotifyAccess=all\n"
        "TimeoutStartSec=20\n"
        "ExecStartPre=-/sbin/modprobe -q fastrpc\n"
        "ExecStartPre=-/sbin/modprobe -q uinput\n"
        "ExecStart=/usr/sbin/tb378fc-sensord\n"
        "ExecStartPost=-/bin/systemctl try-restart --no-block upower.service\n"
        "Restart=always\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n",
        encoding="utf-8",
    )

    iio_drop = stage / "etc/systemd/system/iio-sensor-proxy.service.d"
    iio_drop.mkdir(parents=True, exist_ok=True)
    (iio_drop / "90-tb378fc-sensord.conf").write_text(
        "[Unit]\n"
        "# Defer to tb378fc-sensord when Qualcomm ADSP sensor configs are installed.\n"
        "ConditionPathExists=!/usr/share/qcom/sensors/sns_reg.conf\n",
        encoding="utf-8",
    )

    dbus_dir = stage / "etc/dbus-1/system.d"
    dbus_dir.mkdir(parents=True, exist_ok=True)
    (dbus_dir / "tb378fc-sensord.conf").write_text(
        '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"\n'
        ' "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">\n'
        "<busconfig>\n"
        '  <policy user="root">\n'
        '    <allow own="net.hadess.SensorProxy"/>\n'
        '    <allow send_destination="net.hadess.SensorProxy"/>\n'
        "  </policy>\n"
        '  <policy context="default">\n'
        '    <allow send_destination="net.hadess.SensorProxy"/>\n'
        "  </policy>\n"
        "</busconfig>\n",
        encoding="utf-8",
    )

    modload = stage / "usr/lib/modules-load.d"
    modload.mkdir(parents=True, exist_ok=True)
    (modload / "tb378fc-fastrpc.conf").write_text("fastrpc\n", encoding="utf-8")

    sysusers = stage / "usr/lib/sysusers.d"
    sysusers.mkdir(parents=True, exist_ok=True)
    (sysusers / "tb378fc-fastrpc.conf").write_text(
        'u fastrpc - "Qualcomm FastRPC daemon" /var/lib/fastrpc\n',
        encoding="utf-8",
    )

    tmpfiles = stage / "usr/lib/tmpfiles.d"
    tmpfiles.mkdir(parents=True, exist_ok=True)
    (tmpfiles / "tb378fc-fastrpc.conf").write_text(
        "d /var/lib/fastrpc 0755 fastrpc fastrpc -\n"
        "d /usr/share/qcom/sensors/registry 0755 fastrpc fastrpc -\n",
        encoding="utf-8",
    )

    udev_dir = stage / "usr/lib/udev/rules.d"
    udev_dir.mkdir(parents=True, exist_ok=True)
    (udev_dir / "90-tb378fc-fastrpc.rules").write_text(
        'SUBSYSTEM=="misc", KERNEL=="fastrpc-*", GROUP="fastrpc", MODE="0660"\n'
        'SUBSYSTEM=="misc", KERNEL=="fastrpc-adsp-secure", SYMLINK+="fastrpc-adsp", TAG+="systemd", '
        'ENV{SYSTEMD_WANTS}+="hexagonrpcd-adsp-sensorspd.service"\n'
        'SUBSYSTEM=="misc", KERNEL=="fastrpc-adsp", TAG+="systemd", '
        'ENV{SYSTEMD_WANTS}+="hexagonrpcd-adsp-sensorspd.service"\n',
        encoding="utf-8",
    )

    wants = stage / "etc/systemd/system/multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    for u in ("hexagonrpcd-adsp-sensorspd.service", "tb378fc-sensord.service"):
        (wants / u).symlink_to(f"/usr/lib/systemd/system/{u}")

    # 7. Licenses & source provenance
    doc = stage / "usr/share/doc/tb378fc-sensors"
    doc.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sroot / "hexagonrpc/COPYING", doc / "COPYING.GPL-3.0")
    shutil.copy2(sroot / "qrtr/LICENSE", doc / "LICENSE.qrtr")
    (doc / "source.json").write_text(json.dumps({**lock, "built_revisions": revs}, indent=2) + "\n", encoding="utf-8")

    # 8. DEBIAN metadata
    debian = stage / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    conffiles = sorted(
        "/" + p.relative_to(stage).as_posix()
        for p in (stage / "etc").rglob("*")
        if p.is_file() and not p.is_symlink()
    )
    (debian / "conffiles").write_text("\n".join(conffiles) + "\n", encoding="utf-8")
    (debian / "postinst").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ "$1" = "configure" ]; then\n'
        "    if ! getent group fastrpc >/dev/null 2>&1; then\n"
        "        groupadd --system fastrpc || true\n"
        "    fi\n"
        "    if ! getent passwd fastrpc >/dev/null 2>&1; then\n"
        "        useradd --system --gid fastrpc --home-dir /var/lib/fastrpc "
        "--no-create-home --shell /usr/sbin/nologin fastrpc || true\n"
        "    fi\n"
        "    install -d -m 0755 -o fastrpc -g fastrpc /var/lib/fastrpc /usr/share/qcom/sensors/registry || true\n"
        "    if [ -d /run/systemd/system ]; then\n"
        "        systemctl daemon-reload || true\n"
        "        udevadm control --reload-rules || true\n"
        "    fi\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (debian / "postinst").chmod(0o755)

    installed_kb = sum(
        p.stat().st_size for p in stage.rglob("*") if p.is_file() and not p.is_symlink()
    ) // 1024
    (debian / "control").write_text(
        f"Package: tb378fc-sensors\n"
        f"Version: {args.version}\n"
        f"Architecture: arm64\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Installed-Size: {installed_kb}\n"
        f"Section: admin\n"
        f"Priority: optional\n"
        f"Depends: libc6 (>= 2.38), libsystemd0, libglib2.0-0t64 | libglib2.0-0, coreutils, kmod, systemd\n"
        f"Conflicts: zhiyu-sensord\n"
        f"Replaces: zhiyu-sensord\n"
        f"Description: Lenovo TB378FC Qualcomm ADSP sensor & magnetic folio cover stack\n"
        f" Provides hexagonrpcd, sensh, and tb378fc-sensord for Lenovo Xiaoxin Pad\n"
        f" Pro GT 13 (TB378FC): magnetic folio Hall cover sleep/wake (SW_LID) and\n"
        f" net.hadess.SensorProxy 4-orientation display auto-rotation + ambient light.\n",
        encoding="utf-8",
    )

    deb_path = out_dir / f"tb378fc-sensors_{args.version}_arm64.deb"
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(deb_path)])
    manifest = {
        "status": "OFFLINE_BUILT_NOT_HARDWARE_TESTED",
        "deb": deb_path.name,
        "sha256": sha256(deb_path),
        "size_bytes": deb_path.stat().st_size,
        "version": args.version,
        "revisions": revs,
    }
    (out_dir / "tb378fc-sensors-build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
