#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate and install Lenovo TB378FC vendor-signed firmware and ADSP sensor configs.

Copies owner-extracted stock TB378FC firmware into <rootfs>/lib/firmware and
(when present in --source-dir or --sensors-dir) stages Qualcomm ADSP sensor JSON
configs into <rootfs>/usr/share/qcom/sensors without distributing proprietary
blobs in Git.

Also automatically slices stock Awinic multi-profile `aw882xx_acf.bin` into the
verified single-profile Music register ACF (`awinic/tb378fc/aw88480-native-music.acf`)
expected by mainline `snd-soc-aw88480` on TB378FC.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import sys

TARGETS = {
    "vpu35_4v.mbn": "qcom/sm8750/tb378fc/vpu35_4v.mbn",
    "gen80000_zap.mbn": "qcom/sm8750/tb378fc/gen80000_zap.mbn",
    "gen80000_gmu.bin": "qcom/gen80000_gmu.bin",
    "gen80000_sqe.fw": "qcom/gen80000_sqe.fw",
    "gen80000_aqe.fw": "qcom/gen80000_aqe.fw",
    "adsp.mdt": "qcom/sm8750/tb378fc/adsp.mdt",
    "adsp_dtb.mdt": "qcom/sm8750/tb378fc/adsp_dtb.mdt",
    "cdsp.mdt": "qcom/sm8750/tb378fc/cdsp.mdt",
    "cdsp_dtb.mdt": "qcom/sm8750/tb378fc/cdsp_dtb.mdt",
    "battmgr.jsn": "qcom/sm8750/tb378fc/battmgr.jsn",
    "tb378fc.bin": "novatek/tb378fc.bin",
    "novatek_ts_fw.bin": "novatek/tb378fc.bin",
    "aw88480-native-music.acf": "awinic/tb378fc/aw88480-native-music.acf",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def aw_crc8(payload: bytes) -> int:
    crc = 0
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8C if (crc & 1) else 0)
    return crc


def _parse_aw_reg_bins(blob: bytes) -> list[tuple[bytes, list[tuple[int, int]]]]:
    if len(blob) < 60:
        raise ValueError("AW88480 bin shorter than 60-byte header")
    fields = struct.unpack_from("<15I", blob)
    checksum, version, kind, data_version, size = fields[:5]
    if version != 0x01000000 or data_version != 1 or len(blob) != 60 + size:
        raise ValueError("Unexpected AW88480 bin header version/size")
    if checksum != (sum(blob[4:]) & 0xFFFFFFFF):
        raise ValueError("AW88480 bin checksum mismatch")
    chip = blob[24:32].rstrip(b"\x00")
    if chip != b"AW88480":
        raise ValueError(f"Unexpected Awinic chip tag {chip!r} (expected b'AW88480')")
    if kind == 0x2000:
        count = struct.unpack_from("<I", blob, 60)[0]
        if count > 16 or size < 4 + 8 * count:
            raise ValueError("Invalid AW88480 multi-bin container count")
        result = []
        expected = 4 + 8 * count
        for i in range(count):
            offset, length = struct.unpack_from("<II", blob, 64 + 8 * i)
            if offset != expected or 60 + offset + length > len(blob):
                raise ValueError("Corrupt AW88480 multi-bin offset table")
            result.extend(_parse_aw_reg_bins(blob[60 + offset : 60 + offset + length]))
            expected += length
        if expected != size:
            raise ValueError("Trailing bytes in AW88480 multi-bin container")
        return result
    if kind == 0:
        if fields[8:10] != (2, 2):
            raise ValueError("Unexpected AW88480 reg/val byte widths")
        count = struct.unpack_from("<I", blob, 60)[0]
        if size != 4 + count * 4:
            raise ValueError("AW88480 register bin length mismatch")
        registers = list(struct.iter_unpack("<HH", blob[64:]))
        if len({r for r, _ in registers}) != count:
            raise ValueError("Duplicate registers in AW88480 register bin")
        if not all(3 <= r <= 0x89 for r, _ in registers):
            raise ValueError("Out-of-range register in AW88480 register bin")
        if any(0x76 <= r <= 0x7F for r, _ in registers):
            raise ValueError("Refusing AW88480 bin containing efuse/trim registers 0x76..0x7F")
        return [(blob, registers)]
    return []


def slice_aw88480_music_acf(data: bytes) -> bytes:
    """Extract the 4-channel Music (profile 0) register-only ACF v1 for TB378FC."""
    if len(data) < 80:
        raise ValueError("ACF file shorter than 80-byte ACF v1 header")
    ddt_size, ddt_num, hdr_offset = struct.unpack_from("<III", data, 52)
    if hdr_offset < 80 or ddt_size != ddt_num * 64 or hdr_offset + ddt_size > len(data):
        raise ValueError("Invalid ACF v1 DDT table bounds")

    parts: dict[int, tuple[bytes, list[tuple[int, int]]]] = {}
    for idx in range(ddt_num):
        pos = hdr_offset + idx * 64
        (
            _dde_type,
            _dev_name,
            dev_index,
            _dev_bus,
            _dev_addr,
            dev_profile,
            _data_type,
            data_size,
            data_offset,
            data_crc,
            *_reserved,
        ) = struct.unpack_from("<I16s4H9I", data, pos)
        if dev_profile != 0 or dev_index not in (0, 1, 2, 3):
            continue
        if data_offset + data_size > len(data):
            raise ValueError("ACF DDE payload exceeds file size")
        payload = data[data_offset : data_offset + data_size]
        if aw_crc8(payload) != data_crc:
            raise ValueError(f"ACF CRC8 mismatch on channel {dev_index}")
        reg_bins = _parse_aw_reg_bins(payload)
        if len(reg_bins) != 1:
            raise ValueError(f"Expected 1 register bin for channel {dev_index}, got {len(reg_bins)}")
        parts[dev_index] = reg_bins[0]

    if set(parts) != {0, 1, 2, 3}:
        raise ValueError(f"Missing Music profile channels in ACF (found {sorted(parts)})")

    header = bytearray(data[:80])
    struct.pack_into("<III", header, 52, 4 * 64, 4, 80)
    table = bytearray()
    payloads = bytearray()
    for ch, (blob, registers) in sorted(parts.items()):
        values = dict(registers)
        if values.get(7) != 0xB000 + 0x111 * ch:
            raise ValueError(f"Unexpected TDM slot register {values.get(7):#x} on channel {ch}")
        table += struct.pack(
            "<I16s4H9I",
            2,
            b"aw88480",
            ch,
            0xFFFF,
            0xFFFF,
            0,
            4,
            len(blob),
            80 + 4 * 64 + len(payloads),
            aw_crc8(blob),
            0, 0, 0, 0, 0,
        )
        payloads += blob
    return bytes(header + table + payloads)


def install_sensors(src_roots: list[Path], rootfs: Path) -> int:
    qcom_sensors = rootfs / "usr/share/qcom/sensors"
    cfg_dst = qcom_sensors / "config"
    reg_dst = qcom_sensors / "registry"
    count = 0

    for root in src_roots:
        if not root or not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            parts = f.parts
            if f.suffix == ".json" and "config" in parts and ("sensors" in parts or "sensor" in parts or root.name == "sensors"):
                cfg_dst.mkdir(parents=True, exist_ok=True)
                dst = cfg_dst / f.name
                shutil.copy2(f, dst)
                dst.chmod(0o644)
                count += 1
            elif f.name == "json.lst":
                cfg_dst.mkdir(parents=True, exist_ok=True)
                dst = cfg_dst / "json.lst"
                shutil.copy2(f, dst)
                dst.chmod(0o644)
                count += 1
            elif f.name == "sns_reg.conf" or (f.name == "sns_reg_config" and "registry" not in parts and not f.read_bytes().lstrip().startswith(b"{")):
                qcom_sensors.mkdir(parents=True, exist_ok=True)
                dst = qcom_sensors / "sns_reg.conf"
                shutil.copy2(f, dst)
                dst.chmod(0o644)
                count += 1
            elif "registry" in parts and f.name != "sns_reg_version":
                reg_dst.mkdir(parents=True, exist_ok=True)
                dst = reg_dst / f.name
                shutil.copy2(f, dst)
                dst.chmod(0o644)
                count += 1

    # Generate json.lst automatically if stock JSON configs were copied without json.lst
    if cfg_dst.is_dir() and not (cfg_dst / "json.lst").is_file():
        jsons = sorted(p.name for p in cfg_dst.glob("*.json"))
        if jsons:
            (cfg_dst / "json.lst").write_text("\n".join(jsons) + "\n", encoding="utf-8")
            count += 1
    if cfg_dst.is_dir() and not (qcom_sensors / "sns_reg.conf").is_file():
        (qcom_sensors / "sns_reg.conf").write_text(
            "version=1\n"
            "file=input=json.lst\n"
            "file=output=/mnt/vendor/persist/sensors/registry/registry\n",
            encoding="utf-8",
        )
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory with extracted TB378FC stock firmware/vendor files")
    parser.add_argument("--sensors-dir", type=Path, help="Optional directory with stock /vendor/etc/sensors & /mnt/vendor/persist/sensors")
    parser.add_argument("--rootfs", type=Path, default=Path("/"), help="Target rootfs mountpoint (default: /)")
    args = parser.parse_args()

    src = args.source_dir.resolve()
    rootfs = args.rootfs.resolve()
    fw_root = rootfs / "lib/firmware"
    installed = []

    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        name = f.name
        rel_dst = None
        if name in TARGETS:
            if name == "aw88480-native-music.acf":
                sliced = slice_aw88480_music_acf(f.read_bytes())
                dst = fw_root / TARGETS[name]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(sliced)
                dst.chmod(0o644)
                installed.append((TARGETS[name], dst.stat().st_size, sha256(dst)[:16]))
                continue
            rel_dst = TARGETS[name]
        elif name in ("aw882xx_acf.bin", "aw88480_acf.bin"):
            sliced = slice_aw88480_music_acf(f.read_bytes())
            rel_acf = "awinic/tb378fc/aw88480-native-music.acf"
            dst = fw_root / rel_acf
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(sliced)
            dst.chmod(0o644)
            installed.append((rel_acf, dst.stat().st_size, sha256(dst)[:16]))
            continue
        elif name.startswith(("adsp.b", "adsp_dtb.b", "adspr.", "adsps.", "adspua.", "adspuo.",
                              "cdsp.b", "cdsp_dtb.b", "cdspr.")):
            rel_dst = f"qcom/sm8750/tb378fc/{name}"
        elif name in ("amss.bin", "m3.bin", "board-2.bin", "regdb.bin", "qdss.cfg", "aux_ucode.bin", "bdwlan.elf"):
            rel_dst = f"ath12k/WCN7860/hw2.0/{name}"
        elif name == "phy_ucode20.elf" and not (fw_root / "ath12k/WCN7860/hw2.0/m3.bin").exists():
            rel_dst = "ath12k/WCN7860/hw2.0/m3.bin"
        elif name.startswith(("gngbtfw", "gngbtnv", "brhbtfw", "brhbtnv", "brhperifw", "brhperinv")):
            rel_dst = f"qca/{name}"
        if rel_dst:
            dst = fw_root / rel_dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
            dst.chmod(0o644)
            installed.append((rel_dst, dst.stat().st_size, sha256(dst)[:16]))
            if name == "battmgr.jsn":
                alt = fw_root / "qcom/sm8750/battmgr.jsn"
                alt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, alt)
                alt.chmod(0o644)

    sensor_roots = [src]
    if args.sensors_dir:
        sensor_roots.append(args.sensors_dir.resolve())
    sensor_count = install_sensors(sensor_roots, rootfs)

    if not installed and sensor_count == 0:
        print(f"No recognized TB378FC firmware or sensor files found under {src}", file=sys.stderr)
        return 1

    for rel, sz, digest in installed:
        print(f"  [OK] /lib/firmware/{rel} ({sz} B, sha256={digest}...)")
    if sensor_count:
        print(f"  [OK] Staged {sensor_count} Qualcomm ADSP sensor config/registry files into {rootfs / 'usr/share/qcom/sensors'}")
    print(f"Installed {len(installed)} firmware files into {fw_root}")

    required = [
        "qcom/sm8750/tb378fc/vpu35_4v.mbn",
        "qcom/sm8750/tb378fc/gen80000_zap.mbn",
        "qcom/sm8750/tb378fc/adsp.mdt",
        "qcom/sm8750/tb378fc/adsp_dtb.mdt",
        "novatek/tb378fc.bin",
        "awinic/tb378fc/aw88480-native-music.acf",
    ]
    missing = [rel for rel in required if not (fw_root / rel).is_file()]
    if missing:
        print("Still missing vendor-signed TB378FC firmware files:")
        for m in missing:
            print(f"  [MISSING] /lib/firmware/{m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
