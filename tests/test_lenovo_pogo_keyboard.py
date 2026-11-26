# SPDX-License-Identifier: GPL-2.0-only
"""Offline protocol and driver tests for Lenovo TB378FC magnetic pogo keyboard."""
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[1]
PATCH = (
    REPO
    / "kernel/patches/0018-Input-lenovo_pogo_keyboard-fix-report-offsets-PPP-ch.patch"
)
PATCH_0015 = (
    REPO
    / "kernel/patches/0015-Input-lenovo_pogo_keyboard-add-TB378FC-magnetic-pogo.patch"
)
KERNEL_SRC = Path("/root/tb378fc-work/linux-sm8750-erhai/drivers/input/keyboard/lenovo_pogo_keyboard.c")

PPP_FLAG = 0x7E
PPP_ADDR = 0xFF
PPP_ESC = 0x7D
PPP_ESC_XOR = 0x20

SCRAM_DICT = [
    0xB8, 0x02, 0xD9, 0x57, 0xC6, 0x48, 0xC2, 0x13,
    0xD7, 0xA6, 0x30, 0xBF, 0x40, 0xD3, 0x07, 0xCE,
    0xC3, 0xAB, 0xFA, 0x51, 0x62, 0x18, 0xBD, 0x84,
    0x7D, 0xCA, 0xE0, 0xCF, 0xF2, 0xAC, 0xBC, 0x12,
    0xF5, 0x14, 0xD0, 0x72, 0xDC, 0x1A, 0x50, 0x00,
    0x00, 0x32, 0xD1, 0x7B, 0x33, 0xB6, 0x10, 0xDD,
    0x6A, 0x21, 0xEB, 0x7E, 0x52, 0x5B, 0x3D, 0x9F,
    0x85, 0xCB, 0x1F, 0x66, 0x73, 0x96, 0xA1, 0x60,
]


def ppp_encode_wire(payload: bytes, key_idx: int) -> bytes:
    """Reference PPP encoder matching stock lenovo_keyboard.ko PPPFrameEncode."""
    scram = bytes(b ^ SCRAM_DICT[(key_idx + i) & 0x3F] for i, b in enumerate(payload))
    out = bytearray([PPP_FLAG, PPP_FLAG, PPP_ADDR])
    csum = 0

    def append_escaped(byte: int, update_csum: bool) -> None:
        nonlocal csum
        if byte in (PPP_ESC, PPP_FLAG):
            esc_b = byte ^ PPP_ESC_XOR
            out.append(PPP_ESC)
            out.append(esc_b)
            if update_csum:
                csum = (csum + PPP_ESC + esc_b) & 0xFF
        else:
            out.append(byte)
            if update_csum:
                csum = (csum + byte) & 0xFF

    append_escaped(key_idx, True)
    for b in scram:
        append_escaped(b, True)
    append_escaped(csum, False)
    out.append(PPP_FLAG)
    return bytes(out)


def ppp_decode_wire(frame: bytes) -> bytes:
    """Reference PPP decoder matching fixed lenovo_ppp_decode()."""
    if len(frame) < 4 or frame[0] != PPP_FLAG or frame[1] != PPP_ADDR or frame[-1] != PPP_FLAG:
        raise ValueError("bad framing")
    unesc = bytearray()
    wire_contrib = bytearray()
    esc = False
    for b in frame[2:-1]:
        if esc:
            if b not in (PPP_ESC ^ PPP_ESC_XOR, PPP_FLAG ^ PPP_ESC_XOR):
                raise ValueError("bad escape")
            wire_contrib.append((PPP_ESC + b) & 0xFF)
            unesc.append(b ^ PPP_ESC_XOR)
            esc = False
        elif b == PPP_ESC:
            esc = True
        else:
            wire_contrib.append(b)
            unesc.append(b)
    if esc or len(unesc) < 3:
        raise ValueError("truncated frame")
    if (sum(wire_contrib[:-1]) & 0xFF) != unesc[-1]:
        raise ValueError("bad checksum")
    key_idx = unesc[0]
    return bytes(b ^ SCRAM_DICT[(key_idx + i) & 0x3F] for i, b in enumerate(unesc[1:-1]))


def parse_kbd_packet(pkt: bytes) -> tuple[int, list[int]]:
    if len(pkt) < 10 or pkt[0] != 0x11:
        raise ValueError("invalid kbd packet")
    rep = pkt[2:10]
    if 0x01 in rep[2:8]:
        raise ValueError("rollover")
    return rep[0], list(rep[2:8])


def parse_cuskey_packet(pkt: bytes) -> tuple[int, int]:
    if len(pkt) < 6 or pkt[0] != 0x12:
        raise ValueError("invalid cuskey packet")
    return pkt[2] | (pkt[3] << 8), pkt[4] | (pkt[5] << 8)


def parse_touchpad_packet(pkt: bytes) -> tuple[int, list[tuple[int, bool, bool, int, int]]]:
    if len(pkt) < 3 or pkt[0] != 0x13:
        raise ValueError("invalid touchpad packet")
    len_byte = pkt[1]
    nfingers = (len_byte - 1) // 5 if len_byte >= 6 else 0
    nfingers = min(nfingers, 4)
    data_len = 2 + nfingers * 5
    if 1 + data_len > len(pkt):
        raise ValueError("short touchpad packet")
    btns = pkt[2]
    fingers = []
    for i in range(nfingers):
        f = pkt[3 + i * 5 : 8 + i * 5]
        status = f[0]
        slot = status >> 4
        down = bool(status & 0x01)
        valid = bool(status & 0x04)
        x = f[1] | (f[2] << 8)
        y = f[3] | (f[4] << 8)
        fingers.append((slot, down, valid, x, y))
    return btns, fingers


class TestLenovoPogoKeyboard(unittest.TestCase):
    def test_patch_0018_present_and_tracked(self):
        self.assertTrue(PATCH_0015.is_file())
        self.assertTrue(PATCH.is_file())
        patch_text = PATCH.read_text(encoding="utf-8")
        self.assertIn("lenovo_pogo_keyboard.c", patch_text)

    def test_driver_source_fixes_applied(self):
        if not KERNEL_SRC.is_file():
            self.skipTest("Kernel checkout not present at /root/tb378fc-work/linux-sm8750-erhai")
        src = KERNEL_SRC.read_text(encoding="utf-8")

        m = re.search(r"lenovo_hid_keymap\[(\d+)\]\s*=\s*\{([^}]+)\};", src)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 256)
        entries = [int(x) for x in re.findall(r"\b\d+\b", m.group(2))]
        self.assertEqual(len(entries), 256)
        self.assertEqual(entries[0xE0], 29)
        self.assertEqual(entries[0xEB], 163)

        self.assertNotIn("KEY_UNKNOWN,\t\t0x000c03", src)
        for sym in (
            "KEY_SCREENLOCK",
            "KEY_MICMUTE",
            "KEY_TOUCHPAD_TOGGLE",
            "KEY_FN_ESC",
            "KEY_SWITCHVIDEOMODE",
        ):
            self.assertIn(sym, src)

        self.assertIn("if (!kdev || len < 10)", src)
        self.assertIn("rep = &pkt[2];", src)
        self.assertIn("if (!kdev || len < 6)", src)
        self.assertIn("cur[0] = pkt[2] | (pkt[3] << 8);", src)
        self.assertIn("if (!tdev || len < 3)", src)
        self.assertIn("len_byte = pkt[1];", src)
        self.assertIn("const u8 *f = &pkt[3 + i * 5];", src)
        self.assertIn("wire_contrib[LENOVO_KB_MAX_RAW_LEN]", src)

        # Clickpad hygiene: INPUT_PROP_BUTTONPAD without BTN_RIGHT
        self.assertIn("INPUT_PROP_BUTTONPAD", src)
        self.assertNotIn("BTN_RIGHT", src)
        self.assertIn("INPUT_MT_POINTER | INPUT_MT_DROP_UNUSED", src)

    def test_ppp_wire_checksum_with_7d_and_7e_escapes(self):
        key_idx = 0x7D
        target_scram = bytes([0x7D, 0x7E, 0x11, 0x08, 0x00, 0x00, 0x04, 0x00])
        plain_forced = bytes(
            b ^ SCRAM_DICT[(key_idx + i) & 0x3F] for i, b in enumerate(target_scram)
        )
        wire = ppp_encode_wire(plain_forced, key_idx)
        self.assertEqual(wire[:3], bytes([PPP_FLAG, PPP_FLAG, PPP_ADDR]))
        decoded = ppp_decode_wire(wire[1:])
        self.assertEqual(decoded, plain_forced)

    def test_kbd_cuskey_and_touchpad_packet_vectors(self):
        kbd_pkt = bytes([0x11, 0x08, 0x02, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00])
        mod, keys = parse_kbd_packet(kbd_pkt)
        self.assertEqual(mod, 0x02)
        self.assertEqual(keys[0], 0x04)

        cus_pkt = bytes([0x12, 0x04, 0x92, 0x03, 0x00, 0x00])
        self.assertEqual(parse_cuskey_packet(cus_pkt), (0x0392, 0x0000))

        tp_1f = bytes([0x13, 0x06, 0x01, 0x05, 0x00, 0x04, 0x00, 0x02])
        btns, fingers = parse_touchpad_packet(tp_1f)
        self.assertEqual(btns, 0x01)
        self.assertEqual(fingers, [(0, True, True, 0x0400, 0x0200)])

        tp_2f = bytes([
            0x13, 0x0B, 0x00,
            0x05, 0x10, 0x01, 0x20, 0x02,
            0x15, 0x30, 0x03, 0x40, 0x04,
        ])
        btns2, fingers2 = parse_touchpad_packet(tp_2f)
        self.assertEqual(btns2, 0x00)
        self.assertEqual(
            fingers2,
            [(0, True, True, 0x0110, 0x0220), (1, True, True, 0x0330, 0x0440)],
        )

        tp_0f = bytes([0x13, 0x01, 0x00])
        self.assertEqual(parse_touchpad_packet(tp_0f), (0x00, []))


    def test_qcom_geni_serial_tx_en_waits_for_cmd_done(self):
        geni = Path("/root/tb378fc-work/linux-sm8750-erhai/drivers/tty/serial/qcom_geni_serial.c")
        patch_0019 = REPO / "kernel/patches/0019-tty-serial-qcom_geni_serial-deassert-tx-en-on-M_CMD_.patch"
        self.assertTrue(patch_0019.is_file())
        if not geni.is_file():
            self.skipTest("Kernel checkout not present")
        src = geni.read_text(encoding="utf-8")
        self.assertIn("if (port->tx_en_gpio && (m_irq_status & M_CMD_DONE_EN) &&", src)
        self.assertIn("!port->tx_dma_addr && !qcom_geni_serial_main_active(uport))", src)
        self.assertIn("if (!port->tx_dma_addr && !port->tx_en_gpio)", src)

if __name__ == "__main__":
    unittest.main()
