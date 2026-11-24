#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package tb378fc-video-defaults (GStreamer VA dec/enc, Firefox VA-API, Chromium/Chrome Wayland+VA-API)."""
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
        conffiles = sorted(
            "/" + p.relative_to(root).as_posix()
            for p in (root / "etc").rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        if conffiles:
            (ctrl / "conffiles").write_text("\n".join(conffiles) + "\n", encoding="utf-8")
        postinst = ctrl / "postinst"
        postinst.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            'if [ "$1" = "configure" ] && [ -x /usr/local/libexec/tb378fc-sync-chrome-vaapi ]; then\n'
            "    /usr/local/libexec/tb378fc-sync-chrome-vaapi || true\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        postinst.chmod(0o755)
        prerm = ctrl / "prerm"
        prerm.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            'if [ "$1" = "remove" ]; then\n'
            "    rm -f /usr/local/bin/google-chrome-stable /usr/local/bin/google-chrome "
            "/usr/local/share/applications/google-chrome.desktop || true\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        prerm.chmod(0o755)
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
