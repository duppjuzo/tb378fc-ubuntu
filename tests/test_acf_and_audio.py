# SPDX-License-Identifier: GPL-2.0-only
"""Unit tests for AW88480 ACF v1 Music-profile slicer, AudioReach topology build, and sensor configs."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import pytest

REPO = Path(__file__).resolve().parents[1]


def load_fw_tool():
    spec = importlib.util.spec_from_file_location(
        "install_tb378fc_firmware",
        REPO / "tools/install-tb378fc-firmware.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_reg_bin(channel: int, extra_reg: tuple[int, int] | None = None) -> bytes:
    regs = [
        (4, 0xD040),
        (5, 0x01E7),
        (7, 0xB000 + 0x111 * channel),
        (9, 0x00FF),
    ]
    if extra_reg:
        regs.append(extra_reg)
    payload = struct.pack("<I", len(regs)) + b"".join(struct.pack("<HH", r, v) for r, v in regs)
    hdr = bytearray(60)
    struct.pack_into("<5I", hdr, 0, 0, 0x01000000, 0, 1, len(payload))
    hdr[24:31] = b"AW88480"
    struct.pack_into("<II", hdr, 32, 2, 2)
    blob = bytearray(hdr + payload)
    checksum = sum(blob[4:]) & 0xFFFFFFFF
    struct.pack_into("<I", blob, 0, checksum)
    return bytes(blob)


def make_synthetic_stock_acf(fw_mod, include_voice_profile: bool = True, bad_efuse: bool = False) -> bytes:
    header = bytearray(80)
    header[:4] = b"ACF1"
    entries = []
    blobs = bytearray()
    profiles = [0, 1] if include_voice_profile else [0]
    for prof in profiles:
        for ch in range(4):
            extra = (0x78, 0x1234) if (bad_efuse and prof == 0 and ch == 0) else None
            b = make_reg_bin(ch, extra_reg=extra)
            entries.append((ch, prof, b))
    ddt_num = len(entries)
    struct.pack_into("<III", header, 52, ddt_num * 64, ddt_num, 80)
    table = bytearray()
    for ch, prof, b in entries:
        offset = 80 + ddt_num * 64 + len(blobs)
        table += struct.pack(
            "<I16s4H9I",
            2, b"aw88480", ch, 0, 0x34 + ch, prof,
            1, len(b), offset, fw_mod.aw_crc8(b),
            0, 0, 0, 0, 0,
        )
        blobs += b
    return bytes(header + table + blobs)


def test_firmware_installer_copies_stay_identical():
    a = (REPO / "tools/install-tb378fc-firmware.py").read_bytes()
    b = (REPO / "packaging/device-support/tree/usr/local/sbin/tb378fc-install-firmware").read_bytes()
    assert a == b


def test_aw88480_acf_slicer_extracts_music_and_is_idempotent():
    fw = load_fw_tool()
    stock = make_synthetic_stock_acf(fw, include_voice_profile=True)
    sliced = fw.slice_aw88480_music_acf(stock)
    ddt_size, ddt_num, hdr_off = struct.unpack_from("<III", sliced, 52)
    assert (ddt_size, ddt_num, hdr_off) == (256, 4, 80)
    # Idempotence on already-sliced Music ACF
    assert fw.slice_aw88480_music_acf(sliced) == sliced


def test_aw88480_acf_slicer_rejects_efuse_registers():
    fw = load_fw_tool()
    bad = make_synthetic_stock_acf(fw, include_voice_profile=False, bad_efuse=True)
    with pytest.raises(ValueError, match="efuse/trim"):
        fw.slice_aw88480_music_acf(bad)


def test_audioreach_topology_compiles_playback_and_capture():
    if not (shutil_which("m4") and shutil_which("alsatplg")):
        pytest.skip("m4/alsatplg not installed")
    spec = importlib.util.spec_from_file_location("build_debs", REPO / "tools/build-debs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp)
        out_bin = tp / "Lenovo-TB378FC-tplg.bin"
        mod.compile_audioreach_topology(tp, out_bin)
        assert out_bin.is_file() and out_bin.stat().st_size > 2048


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)
