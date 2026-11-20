#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Minimal HTTP forward proxy bound on the host end of USB NCM (192.168.77.10:8080).

Allows the TB378FC tablet to run `apt` over the USB Gadget NCM link without
configuring Windows/WSL2 NAT or giving the tablet a default route. Plain HTTP
APT archive requests are forwarded; HTTPS CONNECT returns 501 to fail fast.

Usage:
    python dev-apt-proxy.py [bind_addr] [port]   # default 0.0.0.0 8080

On the TB378FC tablet:
    apt -o Acquire::http::Proxy="http://192.168.77.10:8080/" update
    apt -o Acquire::http::Proxy="http://192.168.77.10:8080/" install -y drm-info libdrm-tests v4l-utils vulkan-tools
"""

from __future__ import annotations

import socketserver
import sys
import urllib.error
import urllib.request


BIND = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080


class AptForwardProxy(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            self._handle_once()
        except Exception as exc:
            print(f"[tb378fc-apt-proxy] {exc}", file=sys.stderr, flush=True)

    def _handle_once(self) -> None:
        raw_line = self.rfile.readline(8192)
        if not raw_line:
            return
        parts = raw_line.decode("iso-8859-1", errors="replace").strip().split()
        if len(parts) < 3:
            self.wfile.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return

        method, url, _version = parts[0], parts[1], parts[2]
        headers: dict[str, str] = {}
        while True:
            hdr = self.rfile.readline(8192)
            if not hdr or hdr in (b"\r\n", b"\n"):
                break
            decoded = hdr.decode("iso-8859-1", errors="replace").strip()
            if ":" in decoded:
                k, v = decoded.split(":", 1)
                lk = k.strip().lower()
                if lk not in ("proxy-connection", "connection", "keep-alive", "host"):
                    headers[k.strip()] = v.strip()

        if method.upper() == "CONNECT" or not url.startswith("http://"):
            self.wfile.write(
                b"HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            return

        req = urllib.request.Request(url, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.status
                reason = resp.reason or "OK"
                self.wfile.write(f"HTTP/1.1 {status} {reason}\r\n".encode("iso-8859-1"))
                for k, v in resp.getheaders():
                    if k.lower() in ("connection", "transfer-encoding", "keep-alive"):
                        continue
                    self.wfile.write(f"{k}: {v}\r\n".encode("iso-8859-1"))
                self.wfile.write(b"Connection: close\r\n\r\n")
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.wfile.write(
                f"HTTP/1.1 {exc.code} {exc.reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode(
                    "iso-8859-1"
                )
            )
            self.wfile.write(body)


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    with ThreadingTCPServer((BIND, PORT), AptForwardProxy) as srv:
        print(f"TB378FC USB-NCM apt proxy listening on http://{BIND}:{PORT}", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
