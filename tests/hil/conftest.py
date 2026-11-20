#!/usr/bin/env python3
"""Pytest fixtures for TB378FC Hardware-in-the-Loop (HIL) regression tests.

Supports:
  - Running against a live TB378FC tablet over USB NCM (192.168.77.2) or Wi-Fi SSH
  - Pushing local validation scripts/binaries to the tablet (`target.push_file`)
  - Offline `--dry-run` mode to verify suite syntax/collection before hardware arrives
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
from dataclasses import dataclass

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("tb378fc-hil")
    group.addoption(
        "--tb378fc-host",
        action="store",
        default=os.environ.get("TB378FC_HOST", "192.168.77.2"),
        help="TB378FC IP/hostname (default: 192.168.77.2 for USB NCM).",
    )
    group.addoption(
        "--tb378fc-user",
        action="store",
        default=os.environ.get("TB378FC_USER", "root"),
        help="SSH username on TB378FC (default: root).",
    )
    group.addoption(
        "--tb378fc-key",
        action="store",
        default=os.environ.get("TB378FC_SSH_KEY", ""),
        help="Optional SSH private key path.",
    )
    group.addoption(
        "--dry-run",
        action="store_true",
        default=False,
        help="Offline dry-run: skip live SSH checks cleanly when the physical tablet is not connected.",
    )
    group.addoption(
        "--run-active",
        action="store_true",
        default=False,
        help="Opt-in to active hardware tests (e.g., s2idle freeze cycle or GPU stress).",
    )


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str


class TB378FCTarget:
    """Thin SSH/SCP wrapper for TB378FC Ubuntu hardware testing."""

    def __init__(self, host: str, user: str, key: str, dry_run: bool, run_active: bool) -> None:
        self.host = host
        self.user = user
        self.key = key
        self.dry_run = dry_run
        self.run_active = run_active
        self._ssh_bin = "ssh.exe" if os.name == "nt" else "ssh"
        self._scp_bin = "scp.exe" if os.name == "nt" else "scp"

    def _base_ssh(self, connect_timeout: int = 6) -> list[str]:
        cmd = [
            self._ssh_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self.key:
            cmd.extend(["-i", self.key])
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def require_live(self) -> None:
        if self.dry_run:
            pytest.skip("Offline --dry-run mode active (no physical TB378FC required).")

    def require_active(self) -> None:
        self.require_live()
        if not self.run_active:
            pytest.skip("Active hardware test disabled by default (pass --run-active to enable).")

    def run(self, command: str, *, timeout: int = 30, check: bool = False) -> ExecResult:
        self.require_live()
        proc = subprocess.run(
            self._base_ssh() + [command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        res = ExecResult(
            returncode=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
        )
        if check and res.returncode != 0:
            raise AssertionError(
                f"Remote command failed (rc={res.returncode}): {command}\n"
                f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            )
        return res

    def push_file(self, local_path: str | pathlib.Path, remote_path: str, *, chmod_exec: bool = False) -> None:
        self.require_live()
        p = pathlib.Path(local_path)
        assert p.is_file(), f"Local file missing: {p}"
        remote_dir = remote_path.rsplit("/", 1)[0] if "/" in remote_path else "."
        self.run(f"mkdir -p {shlex.quote(remote_dir)}", check=True)
        cmd = [
            self._scp_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self.key:
            cmd.extend(["-i", self.key])
        cmd.extend([str(p), f"{self.user}@{self.host}:{remote_path}"])
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        assert proc.returncode == 0, f"scp failed: {proc.stderr.decode('utf-8', errors='replace')}"
        if chmod_exec:
            self.run(f"chmod +x {shlex.quote(remote_path)}", check=True)


@pytest.fixture(scope="session")
def tb378fc(request: pytest.FixtureRequest) -> TB378FCTarget:
    target = TB378FCTarget(
        host=request.config.getoption("--tb378fc-host"),
        user=request.config.getoption("--tb378fc-user"),
        key=request.config.getoption("--tb378fc-key"),
        dry_run=request.config.getoption("--dry-run"),
        run_active=request.config.getoption("--run-active"),
    )
    if not target.dry_run:
        probe = target.run("echo TB378FC_READY", timeout=10)
        if probe.returncode != 0 or "TB378FC_READY" not in probe.stdout:
            pytest.exit(
                f"Cannot reach TB378FC via SSH at {target.user}@{target.host}: {probe.stderr.strip()}\n"
                "Hint: pass --dry-run offline, or verify USB NCM (192.168.77.2) / Wi-Fi SSH.",
                returncode=2,
            )
    return target
