#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
'''Ensure WCN7860 UART loads BT firmware after rootfs mount and sets a valid public BD_ADDR.'''
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

# <net/bluetooth/hci.h> on Linux/aarch64
HCIDEVUP = 0x400448C9
HCIDEVDOWN = 0x400448CA
PLACEHOLDER_ADDRS = {
    '00:00:00:00:00:00',
    'ad:5a:01:00:55:78',
    '78:55:00:01:5a:ad',
    '00:00:00:00:5a:ad',
}


def derive_address() -> str:
    serial = Path('/sys/devices/soc0/serial_number').read_bytes().strip()
    if not serial or serial in (b'0', b'00000000'):
        raise RuntimeError('SoC serial number unavailable for Bluetooth address derivation')
    suffix = hashlib.sha256(b'TB378FC Linux RAM Bluetooth test v1\0' + serial).digest()[:5]
    return '02:' + ':'.join(f'{b:02X}' for b in suffix)


def hci_ioctl(req_code: int) -> None:
    AF_BLUETOOTH = getattr(socket, 'AF_BLUETOOTH', 31)
    BTPROTO_HCI = getattr(socket, 'BTPROTO_HCI', 1)
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    try:
        fcntl.ioctl(s.fileno(), req_code, 0)
    except OSError:
        pass
    finally:
        s.close()


def management(*command: str, timeout: int = 10) -> subprocess.CompletedProcess:
    readfd, writefd = os.pipe()
    try:
        return subprocess.run(
            ['btmgmt', '-i', '0', *command],
            stdin=readfd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        os.close(readfd)
        os.close(writefd)


def current_info_addr(info_stdout: str) -> str | None:
    m = re.search(r'\baddr\s+([0-9A-Fa-f:]{17})\b', info_stdout)
    return m.group(1).lower() if m else None


def main() -> None:
    address = derive_address()

    for _ in range(40):
        if Path('/sys/class/bluetooth/hci0').exists() and Path('/lib/firmware/qca/brhbtnv20.bin').exists():
            break
        time.sleep(0.25)

    info_res = management('info')
    cur_addr = current_info_addr(info_res.stdout) if info_res.returncode == 0 else None
    if cur_addr == address.lower():
        record = {
            'status': 'LOCAL_UNIT_ADDRESS_READY',
            'changed': False,
            'address': address,
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        }
        output = Path('/run/tb378fc/bluetooth-address.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record))
        return

    # Force firmware download so WCN7860 transitions out of ROM state
    hci_ioctl(HCIDEVUP)
    time.sleep(0.3)

    changed = False
    for attempt in range(40):
        cfg = management('config')
        if cfg.returncode == 0 and 'missing options:' in cfg.stdout:
            missing_now = cfg.stdout.split('missing options:', 1)[1].splitlines()[0].split()
            if 'public-address' in missing_now:
                # Controller must be powered down for public-addr; leave it down
                # afterwards so bluetoothd can set LE dual-mode & privacy cleanly
                # before AutoEnable=true powers hci0 on.
                hci_ioctl(HCIDEVDOWN)
                res = management('public-addr', address, timeout=15)
                if res.returncode == 0:
                    changed = True
                    break
        info_chk = management('info')
        chk_addr = current_info_addr(info_chk.stdout) if info_chk.returncode == 0 else None
        if chk_addr and chk_addr not in PLACEHOLDER_ADDRS:
            break
        if attempt % 4 == 0:
            hci_ioctl(HCIDEVUP)
        time.sleep(0.25)

    for _ in range(40):
        info_res = management('info')
        chk_addr = current_info_addr(info_res.stdout) if info_res.returncode == 0 else None
        if chk_addr and chk_addr not in PLACEHOLDER_ADDRS:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError('Bluetooth public address update did not complete')

    # Keep hci0 powered down if bluetoothd has not started yet so bluetoothd
    # configures LE privacy/mode without Rejected (0x0b) and powers on via AutoEnable=true.
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'bluetooth.service']).returncode == 0:
        management('power', 'on')

    record = {
        'status': 'LOCAL_UNIT_ADDRESS_READY',
        'changed': changed,
        'address': address,
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    }
    output = Path('/run/tb378fc/bluetooth-address.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
