#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""On-demand PipeWire libcamera -> v4l2loopback webcam bridge for Lenovo TB378FC.

Keeps /dev/video30 (Front Camera) and /dev/video31 (Rear Camera) primed as
standard 1280x720 YUYV exclusive-capture V4L2 devices so V4L2-only desktop apps
(QQ, WeChat, OBS, Electron/WebRTC) detect both cameras immediately, while
keeping the physical MIPI CSI-2 sensors and PipeWire streams 100% idle until a
consumer actually starts capturing (detected via v4l2loopback 0.15+
V4L2_EVENT_PRI_CLIENT_USAGE with zero busy-polling).
"""
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import threading
import time

# AArch64 Linux 64-bit V4L2 ABI constants (verified against linux/videodev2.h)
VIDIOC_S_FMT = 0xC0D05605
VIDIOC_S_PARM = 0xC0CC5616
VIDIOC_SUBSCRIBE_EVENT = 0x4020565A
VIDIOC_DQEVENT = 0x80885659

V4L2_BUF_TYPE_VIDEO_OUTPUT = 2
V4L2_FIELD_NONE = 1
V4L2_COLORSPACE_SRGB = 8
V4L2_PIX_FMT_YUYV = 0x56595559  # 'YUYV'
V4L2_EVENT_SUB_FL_SEND_INITIAL = 1
V4L2_EVENT_PRI_CLIENT_USAGE = 0x08000000 + 0x08E00000 + 1

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30
IDLE_GRACE_SEC = 2.0

# Camera mapping: (loopback_dev, libcamera_location, fallback_pipewire_node_name)
CAMERA_SPECS = [
    (
        "/dev/video30",
        "front",
        "libcamera_input._base_soc_0_cci_ac7c000_i2c-bus_0_camera_31",
    ),
    (
        "/dev/video31",
        "back",
        "libcamera_input._base_soc_0_cci_ac7b000_i2c-bus_0_camera_36",
    ),
]


def make_blank_yuyv(width: int, height: int) -> bytes:
    """Neutral dark YUYV frame (Y=16, U=128, V=128) for clean idle/privacy buffer."""
    return b"\x10\x80" * (width * height)


def configure_v4l2_output(fd: int, width: int, height: int, fps: int) -> None:
    """Configure v4l2loopback OUTPUT format (1280x720 YUYV) and framerate."""
    bpl = width * 2
    sizeimage = bpl * height

    # struct v4l2_format (208 bytes): u32 type, 4B pad, struct v4l2_pix_format at offset 8
    fmt = bytearray(208)
    struct.pack_into("<I", fmt, 0, V4L2_BUF_TYPE_VIDEO_OUTPUT)
    struct.pack_into(
        "<IIIIIIIIIIII",
        fmt,
        8,
        width,
        height,
        V4L2_PIX_FMT_YUYV,
        V4L2_FIELD_NONE,
        bpl,
        sizeimage,
        V4L2_COLORSPACE_SRGB,
        0,
        0,
        0,
        0,
        0,
    )
    fcntl.ioctl(fd, VIDIOC_S_FMT, fmt)

    # struct v4l2_streamparm (204 bytes): u32 type, output.timeperframe at offset 12
    parm = bytearray(204)
    struct.pack_into("<I", parm, 0, V4L2_BUF_TYPE_VIDEO_OUTPUT)
    struct.pack_into("<II", parm, 12, 1, fps)
    try:
        fcntl.ioctl(fd, VIDIOC_S_PARM, parm)
    except OSError:
        pass


def subscribe_client_usage(fd: int) -> None:
    """Subscribe to V4L2_EVENT_PRI_CLIENT_USAGE on v4l2loopback >= 0.13."""
    sub = struct.pack("<III5I", V4L2_EVENT_PRI_CLIENT_USAGE, 0, V4L2_EVENT_SUB_FL_SEND_INITIAL, 0, 0, 0, 0, 0)
    fcntl.ioctl(fd, VIDIOC_SUBSCRIBE_EVENT, sub)


def dequeue_client_usage(fd: int) -> int | None:
    """Dequeue one V4L2_EVENT_PRI_CLIENT_USAGE event and return consumer count."""
    ev = bytearray(136)
    try:
        fcntl.ioctl(fd, VIDIOC_DQEVENT, ev)
    except OSError:
        return None
    ev_type = struct.unpack_from("<I", ev, 0)[0]
    if ev_type != V4L2_EVENT_PRI_CLIENT_USAGE:
        return None
    return struct.unpack_from("<I", ev, 8)[0]


def discover_pipewire_camera(location: str, fallback_node: str) -> str:
    """Resolve current PipeWire libcamera node.name by api.libcamera.location."""
    try:
        raw = subprocess.check_output(["pw-dump"], text=True, timeout=5, stderr=subprocess.DEVNULL)
        objs = json.loads(raw)
        for obj in objs:
            if obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = obj.get("info", {}).get("props", {})
            if props.get("media.class") != "Video/Source":
                continue
            if props.get("api.libcamera.location") == location and props.get("node.name"):
                return str(props["node.name"])
    except Exception:
        pass
    return fallback_node


class VirtualCameraChannel:
    def __init__(self, dev_path: str, location: str, fallback_node: str, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT, fps: int = DEFAULT_FPS):
        self.dev_path = dev_path
        self.location = location
        self.fallback_node = fallback_node
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_size = width * height * 2
        self.blank_frame = make_blank_yuyv(width, height)

        self.fd: int | None = None
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.stop_ev = threading.Event()
        self.active_clients = 0
        self.idle_since: float | None = None

    def open_and_prime(self) -> bool:
        if not Path(self.dev_path).exists():
            print(f"tb378fc-v4l2-camera-bridge: {self.dev_path} missing, skipping", flush=True)
            return False
        self.fd = os.open(self.dev_path, os.O_RDWR | os.O_NONBLOCK)
        configure_v4l2_output(self.fd, self.width, self.height, self.fps)
        os.write(self.fd, self.blank_frame)
        subscribe_client_usage(self.fd)
        print(
            f"tb378fc-v4l2-camera-bridge: primed {self.dev_path} ({self.location}) "
            f"{self.width}x{self.height} YUYV@{self.fps}fps (idle)",
            flush=True,
        )
        return True

    def start_stream(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.fd is None:
            return
        target = discover_pipewire_camera(self.location, self.fallback_node)
        cmd = [
            "gst-launch-1.0",
            "-q",
            "pipewiresrc",
            f"target-object={target}",
            "do-timestamp=true",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,format=YUY2,width={self.width},height={self.height}",
            "!",
            "fdsink",
            "fd=1",
        ]
        self.stop_ev.clear()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self._pump_loop, args=(self.proc,), daemon=True)
        self.thread.start()
        print(f"tb378fc-v4l2-camera-bridge: STARTED {self.dev_path} <- {target} (pid={self.proc.pid})", flush=True)

    def _pump_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        frame_size = self.frame_size
        buf = bytearray(frame_size)
        mv = memoryview(buf)
        frames = 0
        try:
            while not self.stop_ev.is_set():
                got = 0
                while got < frame_size:
                    n = proc.stdout.readinto(mv[got:])
                    if not n:
                        return
                    got += n
                if self.fd is not None and not self.stop_ev.is_set():
                    try:
                        os.write(self.fd, mv)
                        frames += 1
                    except OSError:
                        return
        finally:
            if frames == 0 and proc.stderr is not None and not self.stop_ev.is_set():
                try:
                    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                    if err:
                        print(f"tb378fc-v4l2-camera-bridge: pipeline stderr ({self.dev_path}): {err[:300]}", flush=True)
                except Exception:
                    pass

    def stop_stream(self) -> None:
        self.stop_ev.set()
        proc = self.proc
        self.proc = None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
        if self.thread is not None:
            self.thread.join(timeout=1.0)
            self.thread = None
        if self.fd is not None:
            try:
                os.write(self.fd, self.blank_frame)
            except OSError:
                pass
        print(f"tb378fc-v4l2-camera-bridge: STOPPED {self.dev_path} (sensor released)", flush=True)

    def handle_usage(self, count: int) -> None:
        self.active_clients = count
        if count > 0:
            self.idle_since = None
            self.start_stream()
        else:
            if self.proc is not None:
                self.idle_since = time.monotonic()

    def check_idle_timeout(self) -> None:
        if self.active_clients == 0 and self.idle_since is not None and self.proc is not None:
            if time.monotonic() - self.idle_since >= IDLE_GRACE_SEC:
                self.idle_since = None
                self.stop_stream()

    def close(self) -> None:
        self.stop_stream()
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def main() -> int:
    uid = os.geteuid()
    if uid < 1000 or uid >= 60000:
        return 0
    running = True

    def _stop(*_args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Wait briefly for v4l2loopback nodes to appear at boot
    for _ in range(20):
        if any(Path(spec[0]).exists() for spec in CAMERA_SPECS):
            break
        time.sleep(0.5)

    channels: dict[int, VirtualCameraChannel] = {}
    poller = select.poll()
    for dev_path, loc, fallback in CAMERA_SPECS:
        ch = VirtualCameraChannel(dev_path, loc, fallback)
        try:
            if ch.open_and_prime() and ch.fd is not None:
                channels[ch.fd] = ch
                poller.register(ch.fd, select.POLLPRI)
        except OSError as exc:
            print(f"tb378fc-v4l2-camera-bridge: failed to open {dev_path}: {exc}", flush=True)

    if not channels:
        print("tb378fc-v4l2-camera-bridge: no v4l2loopback devices available; exiting", flush=True)
        return 1

    try:
        while running:
            has_pending_idle = any(ch.idle_since is not None for ch in channels.values())
            timeout_ms = 250 if has_pending_idle else 2000
            try:
                events = poller.poll(timeout_ms)
            except InterruptedError:
                continue
            for fd, mask in events:
                if mask & select.POLLPRI:
                    ch = channels.get(fd)
                    if ch is None:
                        continue
                    while True:
                        count = dequeue_client_usage(fd)
                        if count is None:
                            break
                        if count > 0:
                            # Immediately release any peer camera still in its idle grace window
                            # so Front <-> Rear switching in QQ/WeChat never hits CAMSS -EBUSY.
                            for peer in channels.values():
                                if peer is not ch and peer.active_clients == 0 and peer.proc is not None:
                                    peer.idle_since = None
                                    peer.stop_stream()
                        ch.handle_usage(count)
            for ch in channels.values():
                ch.check_idle_timeout()
                # Recover if gst-launch exited unexpectedly while consumer is still active
                if ch.active_clients > 0 and ch.proc is not None and ch.proc.poll() is not None:
                    print(f"tb378fc-v4l2-camera-bridge: restarting exited stream for {ch.dev_path}", flush=True)
                    ch.stop_stream()
                    time.sleep(0.3)
                    ch.start_stream()
    finally:
        for ch in channels.values():
            ch.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
