#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package tb378fc-video-defaults (GStreamer VA, Firefox VA-API, Chromium Wayland/VA-API)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = REPO / "packaging/video-defaults"
    version = (src / "VERSION").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(dir=out) as td:
        root = Path(td) / "pkg"
        shutil.copytree(src / "tree", root)
        ctrl = root / "DEBIAN"
        ctrl.mkdir()
        (ctrl / "control").write_text(
            (src / "control").read_text(encoding="utf-8").replace("@VERSION@", version),
            encoding="utf-8",
        )
        doc = root / "usr/share/doc/tb378fc-video-defaults"
        doc.mkdir(parents=True)
        shutil.copy2(REPO / "packaging/iris-vaapi/README.md", doc / "README.md")
        deb = out / f"tb378fc-video-defaults_{version}_all.deb"
        env = dict(os.environ, SOURCE_DATE_EPOCH="1794355200")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(deb)], check=True, env=env)
        print(f"Built {deb} ({deb.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
