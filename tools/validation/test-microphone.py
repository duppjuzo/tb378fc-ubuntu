#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bounded on-device dual-microphone capture diagnostic for Lenovo TB378FC."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import time
import wave


def run(argv, timeout=15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--card", default="0")
    p.add_argument("--seconds", type=int, default=4)
    p.add_argument("--ucm-mic", type=Path, default=Path("/usr/share/alsa/ucm2/Qualcomm/sm8750/Lenovo-TB378FC/Mic.conf"))
    p.add_argument("--output", type=Path, default=Path("/run/evidence/microphone"))
    args = p.parse_args()

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    state_file = out / "mixer-before.state"
    run(["alsactl", "-f", str(state_file), "store", args.card]).check_returncode()

    record = {"status": "STARTING", "card": args.card, "seconds": args.seconds}
    try:
        enable = args.ucm_mic.read_text(encoding="utf-8").split("EnableSequence [", 1)[1].split("]", 1)[0]
        for ctl in re.findall(r'cset "([^"]+)"', enable):
            run(["amixer", "-c", args.card, "cset", *shlex.split(ctl)]).check_returncode()

        wav_path = out / "capture.wav"
        cap = subprocess.Popen(
            [
                "arecord", "-D", f"hw:{args.card},2",
                "-f", "S16_LE", "-r", "48000", "-c", "2",
                "-t", "wav", "-d", str(args.seconds), str(wav_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(0.2)
        dapm = {}
        dapm_root = Path("/sys/kernel/debug/asoc/Lenovo-TB378FC")
        if dapm_root.is_dir():
            for name in (
                "MultiMedia3 Mixer", "TX_CODEC_DMA_TX_3 Capture",
                "TX SWR_INPUT4", "TX SWR_INPUT6",
                "ADC1", "ADC3", "MIC BIAS1", "MIC BIAS4",
            ):
                for dp in dapm_root.rglob(name):
                    if dp.is_file():
                        dapm[str(dp)] = dp.read_text(encoding="utf-8", errors="replace")
        record["dapm_active"] = dapm
        out_log, _ = cap.communicate(timeout=args.seconds + 8)
        record["arecord_output"] = out_log
        if cap.returncode != 0:
            raise RuntimeError(f"arecord exited with {cap.returncode}: {out_log}")

        with wave.open(str(wav_path), "rb") as w:
            ch_cnt, samp_w, rate, nframes, *_ = w.getparams()
            raw = w.readframes(nframes)
        samples = array.array("h", raw)
        stats = []
        for ch in range(ch_cnt):
            vals = samples[ch::ch_cnt]
            mean = sum(vals) / max(len(vals), 1)
            rms = math.sqrt(sum((x - mean) ** 2 for x in vals) / max(len(vals), 1))
            stats.append({
                "channel": ch,
                "rms_dbfs": round(20 * math.log10(max(rms, 1e-9) / 32768.0), 2),
                "peak": max((abs(x) for x in vals), default=0),
                "distinct_samples": len(set(vals)),
            })
        record.update(
            status="CAPTURED",
            rate=rate,
            channels=ch_cnt,
            sample_width=samp_w,
            frames=nframes,
            sha256=hashlib.sha256(raw).hexdigest(),
            channel_stats=stats,
        )
    finally:
        run(["alsactl", "-f", str(state_file), "restore", args.card])

    (out / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
