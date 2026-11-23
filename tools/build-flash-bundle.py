#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package the standardized, redistributable TB378FC One-Click Flash Bundle.

Produces:
  1. Full extracted bundle directory (`<output-dir>/bundle/`):
     - install.py (pure-Python cross-platform wizard)
     - flash-windows.bat / flash-linux.sh
     - README-FLASH.zh-CN.md
     - boot/ubuntu/{boot,init_boot,vendor_boot}.raw.img (unsigned Header v4)
     - boot/setup/{boot,init_boot,vendor_boot}.raw.img  (unsigned Header v4)
     - bin/arm64/{unsquashfs,busybox}
     - ksu-template/{tb378fc-switch,ksu-module/*}
     - debs/*.deb
     - rootfs/ubuntu-26.04-tb378fc-v<ver>-arm64-rootfs.squashfs.part00..partNN
     - bundle-manifest.json & SHA256SUMS.txt
  2. GitHub Release upload directory (`<output-dir>/release/`):
     - tb378fc-ubuntu-v<ver>-flash-kit.zip (all scripts, boot, bin, ksu, debs)
     - ubuntu-26.04-tb378fc-v<ver>-arm64-rootfs.squashfs.part00..partNN (<2 GiB each)
     - SHA256SUMS.txt

Runs an automated privacy & proprietary-blob audit before succeeding.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parent.parent

FORBIDDEN_FILENAMES = {
    "vpu35_4v.mbn",
    "adsp.mdt",
    "adsp_dtb.mdt",
    "cdsp.mdt",
    "cdsp_dtb.mdt",
    "battmgr.jsn",
    "aw882xx_acf.bin",
    "aw88480_acf.bin",
    "aw88480-native-music.acf",
    "novatek_ts_fw.bin",
    "amss.bin",
    "bdwlan.elf",
    "local-avb-rsa4096.json",
}

FORBIDDEN_SUBSTRINGS = (
    b"-----BEGIN " + b"RSA PRIVATE KEY-----",
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
    b"-----BEGIN " + b"EC PRIVATE KEY-----",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def split_file(src: Path, dst_prefix: Path, chunk_bytes: int) -> list[Path]:
    dst_prefix.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with src.open("rb") as fin:
        idx = 0
        while True:
            part_path = Path(f"{dst_prefix}.part{idx:02d}")
            written = 0
            with part_path.open("wb") as fout:
                while written < chunk_bytes:
                    buf = fin.read(min(16 << 20, chunk_bytes - written))
                    if not buf:
                        break
                    fout.write(buf)
                    written += len(buf)
            if written == 0:
                part_path.unlink(missing_ok=True)
                break
            parts.append(part_path)
            idx += 1
            if written < chunk_bytes:
                break
    return parts


def build_unsigned_triplet(
    kernel: Path,
    dtb: Path,
    initramfs: Path,
    mkbootimg: Path,
    out_dir: Path,
    extra_args: list[str] | None = None,
) -> None:
    tmp_out = out_dir.parent / (out_dir.name + "-tmp")
    shutil.rmtree(tmp_out, ignore_errors=True)
    cmd = [
        sys.executable,
        str(REPO / "tools/build-boot-triplet.py"),
        "--kernel", str(kernel),
        "--dtb", str(dtb),
        "--initramfs", str(initramfs),
        "--mkbootimg", str(mkbootimg),
        "--unsigned",
        "--output", str(tmp_out),
    ]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in ("boot", "init_boot", "vendor_boot"):
        shutil.move(str(tmp_out / f"{p}.img"), str(out_dir / f"{p}.raw.img"))
    shutil.rmtree(tmp_out, ignore_errors=True)


def write_launchers_and_readme(bundle_dir: Path, version: str) -> None:
    bat = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "where python >nul 2>nul\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo [ERROR] Python 3.10+ not found on PATH. Please install Python 3 from python.org.\r\n"
        "    pause\r\n"
        "    exit /b 1\r\n"
        ")\r\n"
        "python install.py %*\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
    (bundle_dir / "flash-windows.bat").write_bytes(bat.encode("utf-8"))

    sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cd -- \"$(dirname -- \"$0\")\"\n"
        "exec python3 ./install.py \"$@\"\n"
    )
    sh_path = bundle_dir / "flash-linux.sh"
    sh_path.write_text(sh, encoding="utf-8")
    sh_path.chmod(0o755)

    readme = f"""# 联想小新 Pad Pro GT 13 (TB378FC) — Ubuntu 26.04 一键刷机包 (v{version})

本刷机包为 **TB378FC (`SM8750P`)** 标准通用一键刷机与同槽双系统安装包，**不含任何联想/高通闭源受限固件或个人私钥**——刷机脚本会在安装过程中直接从你自己的平板原厂分区提取本机签名固件并完成本地 AVB 签名。

## 1. 刷机前准备
1. 平板已解锁 Bootloader，当前运行原厂 **ZUXOS**（支持 `_a` 或 `_b` 任意活动槽位）。
2. 已在 ZUXOS 中安装 **KernelSU**，开启「开发者选项 → USB 调试」，并在 KernelSU 的「超级用户」列表中授权 **`Shell (com.android.shell)`** root 权限。
3. 电脑已安装 **Python 3.10+**，且环境变量 `PATH`（或本目录 `./platform-tools/`）中包含 `adb` 与 `fastboot`。
4. 若从 GitHub Release 下载，请确保 `ubuntu-26.04-tb378fc-v{version}-arm64-rootfs.squashfs.part00` 与 `.part01` 放在本目录或 `./rootfs/` 子目录下。

## 2. 一键刷机步骤
- **Windows 用户**：双击运行 `flash-windows.bat`
- **Linux / macOS 用户**：终端执行 `./flash-linux.sh`

### 菜单模式说明
- **`[1] 首次全新双系统装机`**：
  - **阶段 1（未分区时自动执行）**：自动备份本机原厂启动分区与 GPT 表至 `backups/<序列号>/` → 启动临时 RAM 分区环境无损缩容 `userdata` 并创建 `ubuntu` 分区（默认 `120 GiB`）→ 自动回到 Fastboot 恢复原厂启动三件套并格式化新 `userdata`（**首次分区会清空 Android 数据，请提前备份！**）。
  - 开机完成 ZUXOS 初始设置、开启 USB 调试并配置好 KernelSU 后，**再次运行脚本**即自动进入阶段 2。
- **`[2] 已有 Ubuntu 分区一键重装`**：
  - 不动 GPT 分区表、不清空 Android 数据；自动校验合并 RootFS、提取本机闭源签名固件与传感器配置、写入 Ubuntu 26.04 (v{version})、生成本机专属 AVB 签名启动三件套，并自动安装激活 KernelSU 一键切换模块。
- **`[3] 不格盘原地升级`**：保留 Ubuntu 内全部个人文件与设置，原地升级全部 `.deb` 驱动包、Linux 7.4 内核启动镜像与双系统切换器。
- **`[5] Fastboot 紧急恢复原厂`**：长按「电源键 + 音量下」进入 Fastboot 后一键刷回 `backups/<序列号>/` 中的原厂备份。

## 3. 日常双系统一键互切
- **Android → Ubuntu**：打开 **KernelSU → 模块 → TB378FC Switch → 点击「操作 (Action)」** 自动切入 Ubuntu。
- **Ubuntu → Android**：点击 Ubuntu 应用列表中的 **「切换到 Android」** 图标自动切回 ZUXOS。
"""
    (bundle_dir / "README-FLASH.zh-CN.md").write_text(readme, encoding="utf-8")


def audit_bundle_hygiene(bundle_dir: Path) -> None:
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in FORBIDDEN_FILENAMES or p.suffix in (".pem", ".key"):
            raise SystemExit(f"SECURITY AUDIT FAILED: Forbidden file in bundle: {p}")
        # Scan text/small files (<32 MiB) for private keys or hardcoded unit serials
        if p.stat().st_size <= (32 << 20):
            raw = p.read_bytes()
            for needle in FORBIDDEN_SUBSTRINGS:
                if needle in raw:
                    raise SystemExit(
                        f"SECURITY AUDIT FAILED: Forbidden secret/serial {needle!r} found in {p}"
                    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True, help="Release version string (e.g. 0.6.5)")
    ap.add_argument("--kernel", type=Path, required=True)
    ap.add_argument("--dtb", type=Path, required=True)
    ap.add_argument("--prod-initramfs", type=Path, required=True)
    ap.add_argument("--setup-initramfs", type=Path, required=True)
    ap.add_argument("--mkbootimg", type=Path, required=True)
    ap.add_argument("--unsquashfs-arm64", type=Path, required=True)
    ap.add_argument("--busybox-arm64", type=Path, required=True)
    ap.add_argument("--debs-dir", type=Path, required=True)
    ap.add_argument("--rootfs-squashfs", type=Path, required=True)
    ap.add_argument("--split-mib", type=int, default=1900, help="Max MiB per split RootFS file (<2000 for GitHub Releases)")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    if args.split_mib >= 2000:
        raise SystemExit("--split-mib must be < 2000 MiB to fit GitHub Releases 2 GiB per-asset limit")

    out_root = args.output_dir.resolve()
    bundle_dir = out_root / f"tb378fc-ubuntu-v{args.version}-flash-bundle"
    rel_dir = out_root / "github-release"
    shutil.rmtree(bundle_dir, ignore_errors=True)
    shutil.rmtree(rel_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True)
    rel_dir.mkdir(parents=True)

    # 1. Copy standalone wizard & launchers
    install_py = bundle_dir / "install.py"
    shutil.copy2(REPO / "tools/flash-tb378fc.py", install_py)
    install_py.chmod(0o755)
    write_launchers_and_readme(bundle_dir, args.version)

    # 2. Build unsigned Header v4 boot triplets (ubuntu + setup)
    build_unsigned_triplet(
        args.kernel, args.dtb, args.prod_initramfs, args.mkbootimg, bundle_dir / "boot/ubuntu"
    )
    build_unsigned_triplet(
        args.kernel,
        args.dtb,
        args.setup_initramfs,
        args.mkbootimg,
        bundle_dir / "boot/setup",
        extra_args=["--cmdline-extra", "tb378fc.setup=1 tb378fc.autosplit=120"],
    )

    # 3. Stage static ARM64 helpers
    arm_bin = bundle_dir / "bin/arm64"
    arm_bin.mkdir(parents=True)
    shutil.copy2(args.unsquashfs_arm64, arm_bin / "unsquashfs")
    shutil.copy2(args.busybox_arm64, arm_bin / "busybox")
    (arm_bin / "unsquashfs").chmod(0o755)
    (arm_bin / "busybox").chmod(0o755)

    # 4. Stage KSU switch template
    ksu_tpl = bundle_dir / "ksu-template"
    shutil.copytree(REPO / "switch/ksu-module", ksu_tpl / "ksu-module")
    shutil.copy2(REPO / "switch/tb378fc-switch", ksu_tpl / "tb378fc-switch")
    (ksu_tpl / "tb378fc-switch").chmod(0o755)

    # 5. Stage verified .deb packages
    debs_dst = bundle_dir / "debs"
    debs_dst.mkdir(parents=True)
    deb_list = sorted(args.debs_dir.resolve().glob("*.deb"))
    if not deb_list:
        raise SystemExit(f"No .deb files found in {args.debs_dir}")
    for d in deb_list:
        shutil.copy2(d, debs_dst / d.name)

    # 6. Split clean RootFS SquashFS into <2 GiB parts
    sq_src = args.rootfs_squashfs.resolve()
    rootfs_sha = sha256_file(sq_src)
    rootfs_bytes = sq_src.stat().st_size
    base_sq_name = f"ubuntu-26.04-tb378fc-v{args.version}-arm64-rootfs.squashfs"
    parts = split_file(sq_src, bundle_dir / "rootfs" / base_sq_name, args.split_mib * 1024 * 1024)

    # 7. Compute file manifest (excluding SHA256SUMS.txt & bundle-manifest.json themselves)
    files_map = {}
    for p in sorted(bundle_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(bundle_dir).as_posix()
            files_map[rel] = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}

    manifest = {
        "bundle_format": 1,
        "device": "Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)",
        "version": f"v{args.version}",
        "created_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rootfs_squashfs_name": base_sq_name,
        "rootfs_bytes": rootfs_bytes,
        "rootfs_sha256": rootfs_sha,
        "proprietary_vendor_blobs_included": False,
        "files": files_map,
    }
    (bundle_dir / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    sums_lines = [
        f"{spec['sha256']}  {rel}" for rel, spec in sorted(files_map.items())
    ]
    sums_lines.append(f"{sha256_file(bundle_dir / 'bundle-manifest.json')}  bundle-manifest.json")
    (bundle_dir / "SHA256SUMS.txt").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    # 8. Run mandatory security & hygiene audit BEFORE producing release artifacts
    audit_bundle_hygiene(bundle_dir)

    # 9. Assemble GitHub Release upload kit (<2 GiB per asset)
    kit_zip = rel_dir / f"tb378fc-ubuntu-v{args.version}-flash-kit.zip"
    with zipfile.ZipFile(kit_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(bundle_dir.rglob("*")):
            if p.is_file() and not p.relative_to(bundle_dir).as_posix().startswith("rootfs/"):
                arcname = f"tb378fc-ubuntu-v{args.version}-flash-bundle/" + p.relative_to(bundle_dir).as_posix()
                zf.write(p, arcname)

    rel_assets = [kit_zip]
    for part in parts:
        dst_part = rel_dir / part.name
        shutil.copy2(part, dst_part)
        rel_assets.append(dst_part)

    rel_sums = [f"{sha256_file(a)}  {a.name}" for a in rel_assets]
    (rel_dir / "SHA256SUMS.txt").write_text("\n".join(rel_sums) + "\n", encoding="utf-8")

    print(f"[OK] Built verified flash bundle: {bundle_dir}")
    print(f"[OK] Built GitHub Release assets: {rel_dir}")
    for a in (*rel_assets, rel_dir / "SHA256SUMS.txt"):
        print(f"     - {a.name} ({a.stat().st_size / (1024**2):.2f} MiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
