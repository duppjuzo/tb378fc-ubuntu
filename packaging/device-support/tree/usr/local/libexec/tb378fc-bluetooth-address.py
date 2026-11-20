#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Set a deterministic locally-administered BT address when controller lacks one."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


def derive_address():
    serial = Path('/sys/devices/soc0/serial_number').read_bytes().strip()
    if not serial or serial in (b'0', b'00000000'):
        raise RuntimeError('SoC serial number unavailable for Bluetooth address derivation')
    suffix = hashlib.sha256(b'TB378FC Linux RAM Bluetooth test v1\0' + serial).digest()[:5]
    return '02:' + ':'.join(f'{b:02X}' for b in suffix)


def management(*command, timeout=6):
    # BlueZ btmgmt stalls on /dev/null immediate HUP under systemd.
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


def main():
    address = derive_address()
    result = None
    for _ in range(40):
        result = management('config')
        if result.returncode == 0 and 'missing options:' in result.stdout:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError('Native Bluetooth controller hci0 did not register')

    missing = result.stdout.split('missing options:', 1)[1].splitlines()[0].split()
    changed = False
    if 'public-address' in missing:
        res = management('public-addr', address, timeout=12)
        res.check_returncode()
        changed = True
        for _ in range(40):
            info_res = management('info')
            info_res.check_returncode()
            if re.search(r'\baddr ' + re.escape(address) + r'\b', info_res.stdout, re.I):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Bluetooth public address update did not complete')

    record = {
        'status': 'LOCAL_UNIT_ADDRESS_READY',
        'changed': changed,
        'address': address if changed else 'existing',
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    }
    output = Path('/run/tb378fc/bluetooth-address.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
