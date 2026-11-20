#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Own one native torch with a fixed current and a 30-second deadline."""
import grp
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import struct
import subprocess
import time

LED = Path('/sys/class/leds/white:flash:tb378fc')


def active_seat_uid():
    """Return the UID of the active local seat0 session, or None."""
    try:
        sid = subprocess.check_output(
            ['loginctl', 'show-seat', 'seat0', '-p', 'ActiveSession', '--value'],
            text=True, timeout=4,
        ).strip()
        if not sid:
            return None
        text = subprocess.check_output(
            ['loginctl', 'show-session', sid, '-p', 'User', '-p', 'Active', '-p', 'Class'],
            text=True, timeout=4,
        )
        info = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if info.get('Active') == 'yes' and info.get('Class') in ('user', 'user-early'):
            return int(info['User'])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def peer_authorized(uid, gid=None):
    """Authorize root, active seat0 desktop user, or members of the video group."""
    if uid == 0:
        return True
    seat_uid = active_seat_uid()
    if seat_uid is not None and uid == seat_uid:
        return True
    try:
        video_grp = grp.getgrnam('video')
        if gid is not None and gid == video_grp.gr_gid:
            return True
        account = pwd.getpwuid(uid)
        if account.pw_gid == video_grp.gr_gid or account.pw_name in video_grp.gr_mem:
            return True
    except KeyError:
        pass
    return False


def main():
    assert int((LED / 'max_brightness').read_text()) == 255
    assert int((LED / 'max_flash_brightness').read_text()) >= 100000
    assert int(os.environ.get('LISTEN_PID', '0')) == os.getpid()
    assert os.environ.get('LISTEN_FDS') == '1'
    server = socket.socket(fileno=3)
    server.settimeout(0.25)
    deadline = None
    running = True

    def write(name, value):
        (LED / name).write_text(str(value) + '\n')

    def settle(state):
        until = time.monotonic() + 0.5
        while int((LED / 'flash_strobe').read_text()) != state:
            if time.monotonic() >= until:
                raise TimeoutError('Torch did not settle')
            time.sleep(0.005)

    def off():
        nonlocal deadline
        write('brightness', 0)
        settle(0)
        write('flash_strobe', 0)
        deadline = None

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    off()
    try:
        while running:
            if deadline is not None and time.monotonic() >= deadline:
                off()
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(0.5)
                try:
                    _, uid, gid = struct.unpack(
                        '3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    if not peer_authorized(uid, gid):
                        raise PermissionError('Active local desktop session or video group required')
                    request = json.loads(connection.recv(1024))
                    operation = request.get('op')
                    if operation == 'toggle':
                        operation = 'off' if deadline is not None else 'on'
                    if operation == 'off':
                        off()
                    elif operation == 'on':
                        # Board DT caps total torch current at 100 mA.
                        deadline = time.monotonic() + 30
                        write('brightness', 255)
                        settle(1)
                    elif operation != 'state':
                        raise ValueError('Unknown operation')
                    response = {
                        'on': deadline is not None,
                        'remaining': max(0, int(deadline - time.monotonic()) + 1) if deadline else 0,
                    }
                except Exception:
                    off()
                    response = {'error': 'Torch unavailable'}
                try:
                    connection.sendall(json.dumps(response).encode() + b'\n')
                except OSError:
                    pass
    finally:
        off()
        server.close()


if __name__ == '__main__':
    main()
