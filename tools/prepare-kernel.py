#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Prepare a patched linux-sm8750-erhai tree at the pinned TB378FC v7.4 baseline."""
import argparse
import json
import pathlib
import subprocess
import sys


def run(cmd, cwd=None):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main():
    repo = pathlib.Path(__file__).resolve().parents[1]
    meta = json.loads((repo / "kernel/source.json").read_text(encoding="utf-8"))
    kmeta = meta["kernel"]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kernel-dir",
        type=pathlib.Path,
        default=repo / "cache/linux-sm8750-erhai",
        help="Destination Linux source checkout (must be on a case-sensitive ext4 filesystem)",
    )
    parser.add_argument(
        "--branch",
        default="tb378fc/v7.4-dev",
        help="Local git branch to create/reset after applying patches",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify all patches apply cleanly with git apply --check without modifying HEAD",
    )
    args = parser.parse_args()

    kdir = args.kernel_dir.resolve()
    commit = kmeta["commit"]
    url = kmeta["repository"]
    patches = [repo / p for p in kmeta["patches"]]
    for p in patches:
        if not p.is_file():
            raise SystemExit(f"Missing kernel patch: {p}")

    if not kdir.exists():
        kdir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", kmeta["branch"], "--depth", "200", url, str(kdir)])

    has_commit = subprocess.run(
        ["git", "-C", str(kdir), "cat-file", "-e", f"{commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not has_commit:
        run(["git", "-C", str(kdir), "fetch", "--depth", "200", "origin", commit])

    if args.check_only:
        cur = subprocess.check_output(
            ["git", "-C", str(kdir), "rev-parse", "HEAD"], text=True
        ).strip()
        if cur != commit:
            raise SystemExit(
                f"--check-only expects HEAD == {commit} (found {cur}); checkout base commit first"
            )
        for p in patches:
            run(["git", "-C", str(kdir), "apply", "--check", str(p)])
        print(f"All {len(patches)} patches verified cleanly against {commit}.")
        return

    run(["git", "-C", str(kdir), "checkout", "-B", args.branch, commit])
    run(["git", "-C", str(kdir), "reset", "--hard", commit])
    run(["git", "-C", str(kdir), "clean", "-fd"])

    # Ensure local git author identity exists for git am
    if subprocess.run(["git", "-C", str(kdir), "config", "user.name"], capture_output=True).returncode != 0:
        run(["git", "-C", str(kdir), "config", "user.name", "TB378FC Builder"])
    if subprocess.run(["git", "-C", str(kdir), "config", "user.email"], capture_output=True).returncode != 0:
        run(["git", "-C", str(kdir), "config", "user.email", "builder@tb378fc.local"])

    run(["git", "-C", str(kdir), "am", "--3way", *[str(p) for p in patches]])
    head = subprocess.check_output(["git", "-C", str(kdir), "rev-parse", "HEAD"], text=True).strip()
    print(f"Prepared TB378FC kernel tree at {kdir} (branch {args.branch}, HEAD {head}).")


if __name__ == "__main__":
    main()
