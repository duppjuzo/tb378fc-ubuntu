#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Unit tests for the pure-Python TB378FC flash wizard, AVB signer, and GPT splitter."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]


def load_flash_module():
    spec = importlib.util.spec_from_file_location("flash_tb378fc", ROOT / "tools/flash-tb378fc.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_minimal_fdt(props_by_node: dict[str, dict[str, bytes]]) -> bytes:
    """Build a valid minimal FDT (v17) for unit-testing verify_dtbo_against_vendor_boot."""
    strings = bytearray()
    str_offs: dict[str, int] = {}

    def get_stroff(s: str) -> int:
        if s not in str_offs:
            str_offs[s] = len(strings)
            strings.extend(s.encode("utf-8") + b"\x00")
        return str_offs[s]

    dt_struct = bytearray()

    def emit_node(name: str, props: dict[str, bytes], children: list[tuple[str, dict[str, bytes]]]):
        dt_struct.extend(struct.pack(">I", 1))  # FDT_BEGIN_NODE
        nb = name.encode("utf-8") + b"\x00"
        dt_struct.extend(nb + b"\x00" * ((4 - len(nb) % 4) % 4))
        for k, v in props.items():
            dt_struct.extend(struct.pack(">3I", 3, len(v), get_stroff(k)))  # FDT_PROP
            dt_struct.extend(v + b"\x00" * ((4 - len(v) % 4) % 4))
        for cname, cprops in children:
            emit_node(cname, cprops, [])
        dt_struct.extend(struct.pack(">I", 2))  # FDT_END_NODE

    root_props = props_by_node.get("/", {})
    child_nodes = [(k.lstrip("/"), v) for k, v in props_by_node.items() if k != "/"]
    emit_node("", root_props, child_nodes)
    dt_struct.extend(struct.pack(">I", 9))  # FDT_END

    off_rsv = 40
    rsv_block = b"\x00" * 16
    off_struct = off_rsv + len(rsv_block)
    off_strings = off_struct + len(dt_struct)
    totalsize = off_strings + len(strings)
    hdr = struct.pack(
        ">10I",
        0xD00DFEED,
        totalsize,
        off_struct,
        off_strings,
        off_rsv,
        17,
        16,
        0,
        len(strings),
        len(dt_struct),
    )
    return hdr + rsv_block + bytes(dt_struct) + bytes(strings)


class TestFlashBundleAndAvb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_flash_module()

    def test_boot_v4_cmdline_customization(self):
        hdr = bytearray(4096)
        hdr[:8] = b"ANDROID!"
        struct.pack_into("<I", hdr, 20, 1584)
        struct.pack_into("<I", hdr, 40, 4)
        raw = bytes(hdr) + b"KERNEL_PAYLOAD" * 100
        updated = self.mod.customize_boot_v4_cmdline(raw, "rdinit=/init tb378fc.root=UUID=1234-5678")
        self.assertEqual(updated[:8], b"ANDROID!")
        self.assertEqual(
            updated[44:1580].rstrip(b"\x00").decode("ascii"),
            "rdinit=/init tb378fc.root=UUID=1234-5678",
        )
        self.assertEqual(updated[4096:], raw[4096:])

    def test_pure_python_avb_footer_roundtrip_and_avbtool_compat(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            rsa_key = self.mod.load_or_create_rsa4096_key(tdir / "key.json")
            raw_img = b"INITBOOT_PAYLOAD" * 256
            part_size = 512 * 1024
            signed = self.mod.add_avb_hash_footer(
                raw_img=raw_img,
                partition_name="init_boot",
                partition_size=part_size,
                rollback_index=1749081600,
                rsa_key=rsa_key,
            )
            self.assertEqual(len(signed), part_size)
            signed_path = tdir / "init_boot.img"
            signed_path.write_bytes(signed)

            info = self.mod.read_avb_footer_info(signed_path)
            self.assertTrue(info["has_footer"])
            self.assertEqual(info["partition_size"], part_size)
            self.assertEqual(info["original_image_size"], len(raw_img))
            self.assertEqual(info["rollback_index"], 1749081600)
            self.assertEqual(info["algorithm_type"], 2)

            # If upstream avbtool.py is available in WSL cache, run full cryptographic verify_image!
            avbtool = Path("/root/tb378fc-work/cache/tools/avbtool.py")
            if avbtool.is_file():
                out = subprocess.check_output(
                    ["python3", str(avbtool), "verify_image", "--image", str(signed_path)],
                    text=True,
                )
                self.assertIn("Verifying image", out)

    def test_dtbo_symbol_coverage_check(self):
        vboot_fdt = _make_minimal_fdt({
            "/": {"qcom,msm-id": b"\x00\x00\x02\x6a\x00\x01\x00\x00", "qcom,board-id": b"\x00\x00\x00\x22\x00\x00\x00\x00"},
            "/__symbols__": {"soc": b"/erhai-abl/overlay-sink\x00", "tlmm": b"/erhai-abl/overlay-sink\x00"},
        })
        dtbo_ok = _make_minimal_fdt({"/__fixups__": {"soc": b"/fragment@0:target:0\x00"}})
        dtbo_bad = _make_minimal_fdt({"/__fixups__": {"unknown_sym": b"/fragment@0:target:0\x00"}})

        cov = self.mod.verify_dtbo_against_vendor_boot(dtbo_ok, b"VNDRBOOT_PAD" + vboot_fdt)
        self.assertEqual(cov["dtbo_entries"], 1)
        self.assertEqual(cov["dtbo_fixup_symbols"], 1)

        with self.assertRaises(RuntimeError):
            self.mod.verify_dtbo_against_vendor_boot(dtbo_bad, b"VNDRBOOT_PAD" + vboot_fdt)

    def test_gpt_splitter_4k_sectors_and_crc(self):
        gcc = shutil.which("gcc")
        if not gcc:
            self.skipTest("host gcc not installed")
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            bin_path = tdir / "tb378fc-gpt-split"
            subprocess.run(
                [gcc, "-O2", "-Wall", "-Werror", str(ROOT / "tools/gpt-split/tb378fc-gpt-split.c"), "-o", str(bin_path)],
                check=True,
            )

            # Construct synthetic 4096-byte-sector GPT sparse file matching 256GB TB378FC LBA bounds
            bsize = 4096
            alt_lba = 61390847
            last_usable = 61390842
            first_usable = 6
            bak_part_lba = 61390843
            img = tdir / "fake-ufs.img"
            with img.open("wb") as f:
                f.truncate((alt_lba + 1) * bsize)

                entries = bytearray(32 * 128)
                e17 = memoryview(entries)[17 * 128 : 18 * 128]
                e17[0:16] = b"\x01" * 16
                e17[16:32] = b"\x02" * 16
                struct.pack_into("<QQQ", e17, 32, 6365608, 61390842, 0)
                name_u16 = "userdata".encode("utf-16le").ljust(72, b"\x00")
                e17[56:128] = name_u16
                ent_crc = zlib.crc32(entries) & 0xFFFFFFFF

                pri = bytearray(bsize)
                struct.pack_into("<8sIIIIQQQQ16sQIII", pri, 0, b"EFI PART", 0x00010000, 92, 0, 0, 1, alt_lba, first_usable, last_usable, b"\xaa" * 16, 2, 32, 128, ent_crc)
                struct.pack_into("<I", pri, 16, zlib.crc32(pri[:92]) & 0xFFFFFFFF)

                bak = bytearray(bsize)
                struct.pack_into("<8sIIIIQQQQ16sQIII", bak, 0, b"EFI PART", 0x00010000, 92, 0, 0, alt_lba, 1, first_usable, last_usable, b"\xaa" * 16, bak_part_lba, 32, 128, ent_crc)
                struct.pack_into("<I", bak, 16, zlib.crc32(bak[:92]) & 0xFFFFFFFF)

                f.seek(1 * bsize)
                f.write(pri)
                f.seek(2 * bsize)
                f.write(entries)
                f.seek(bak_part_lba * bsize)
                f.write(entries)
                f.seek(alt_lba * bsize)
                f.write(bak)

            dry = subprocess.check_output([str(bin_path), str(img), "--ubuntu-gib", "120"], text=True)
            self.assertIn("29933568..61390842 (120.00 GiB)", dry)
            self.assertIn("DRY_RUN_COMPLETE", dry)

            applied = subprocess.check_output([str(bin_path), str(img), "--ubuntu-gib", "120", "--apply"], text=True)
            self.assertIn("APPLIED_AND_SYNCED_OK", applied)

            idem = subprocess.check_output([str(bin_path), str(img), "--ubuntu-gib", "120", "--apply"], text=True)
            self.assertIn("ALREADY_SPLIT_OK", idem)


if __name__ == "__main__":
    unittest.main()
