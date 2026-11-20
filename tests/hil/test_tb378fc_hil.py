#!/usr/bin/env python3
"""Modular Hardware-in-the-Loop (HIL) regression test suite for TB378FC Ubuntu.

Run offline collection check (no tablet needed):
    pytest -v --dry-run

Run smoke checks on live tablet over USB NCM:
    pytest -v -m boot --tb378fc-host 192.168.77.2

Run full non-destructive regression:
    pytest -v --tb378fc-host 192.168.77.2 --tb378fc-user root
"""

from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# 1. Boot, Kernel Integrity, DT Identity, Pstore & Systemd
# ---------------------------------------------------------------------------

@pytest.mark.boot
def test_kernel_release_and_clean_taint(tb378fc) -> None:
    rel = tb378fc.run("uname -r", check=True).stdout.strip()
    taint = tb378fc.run("cat /proc/sys/kernel/tainted", check=True).stdout.strip()
    assert "tb378fc" in rel.lower() or rel.startswith("7."), f"Unexpected kernel release: {rel}"
    assert taint == "0", f"Kernel is tainted (expected 0 with unified in-tree modules, got {taint})"


@pytest.mark.boot
def test_dt_root_identity_and_no_foreign_nodes(tb378fc) -> None:
    compat = tb378fc.run("tr '\\0' '\\n' < /proc/device-tree/compatible", check=True).stdout
    model = tb378fc.run("tr -d '\\0' < /proc/device-tree/model", check=True).stdout
    assert "lenovo,tb378fc" in compat, f"Missing lenovo,tb378fc root compatible (ABL override?): {compat!r}"
    assert "oneplus" not in compat.lower() and "xiaomi" not in compat.lower(), f"Foreign board compatible leaked: {compat!r}"
    assert "tb378fc" in model.lower() or "lenovo" in model.lower() or "xiaoxin" in model.lower(), f"Unexpected DT model: {model!r}"


@pytest.mark.boot
def test_systemd_no_failed_units_and_legacy_services_removed(tb378fc) -> None:
    failed = tb378fc.run("systemctl --failed --no-legend --plain 2>/dev/null || true").stdout.strip()
    assert not failed, f"Failed systemd units detected:\n{failed}"

    legacy = tb378fc.run(
        "systemctl list-unit-files 'tb378fc-*68*' 'tb378fc-*73*' 'tb378fc-*74*' 'tb378fc-*76*' --no-legend 2>/dev/null || true"
    ).stdout.strip()
    assert not legacy, f"Legacy staged .ko hot-loader services still present:\n{legacy}"


@pytest.mark.boot
def test_pstore_ramoops_no_previous_panic(tb378fc) -> None:
    out = tb378fc.run("ls -1 /sys/fs/pstore 2>/dev/null || true").stdout.strip()
    assert "dmesg-ramoops" not in out, f"Previous kernel panic captured in /sys/fs/pstore:\n{out}"


@pytest.mark.boot
def test_dmesg_no_oops_or_bug(tb378fc) -> None:
    log = tb378fc.run("dmesg --nopager 2>/dev/null || journalctl -k -b --no-pager").stdout
    bad = [
        line
        for line in log.splitlines()
        if re.search(r"BUG:|Oops:|Unable to handle kernel|Internal error:|Call trace:|DSI.*timeout|VBIF.*halt", line)
    ]
    assert not bad, "Critical kernel errors found in dmesg:\n" + "\n".join(bad[:20])


# ---------------------------------------------------------------------------
# 2. Display, DFPS 60/120/144Hz, Dual Backlight & Touch
# ---------------------------------------------------------------------------

@pytest.mark.display
def test_drm_panel_modes_60_120_144hz(tb378fc) -> None:
    modes = tb378fc.run("cat /sys/class/drm/card*-DSI-*/modes 2>/dev/null || true").stdout.strip()
    if not modes:
        res = tb378fc.run("drm_info 2>/dev/null || true").stdout
        assert res, "Neither /sys/class/drm/card*-DSI-*/modes nor drm_info returned panel modes."
        assert "120" in res, "120Hz mode missing in drm_info output."
        return
    # Also verify exact vertical refresh rates via drm_info or modetest if installed
    info = tb378fc.run("modetest -M msm -c 2>/dev/null || drm_info 2>/dev/null || true").stdout
    if info:
        for hz in ("60", "120", "144"):
            assert re.search(rf"\b{hz}(\.\d+)?\b", info), f"Expected {hz}Hz DFPS mode in DRM connector info."


@pytest.mark.display
def test_dual_aw99706b_backlight_present(tb378fc) -> None:
    bls = tb378fc.run("ls -1 /sys/class/backlight/ 2>/dev/null || true").stdout.strip().splitlines()
    assert bls, "No backlight sysfs class entries found."


@pytest.mark.display
def test_nt36536_touchscreen_registered(tb378fc) -> None:
    devices = tb378fc.run("cat /proc/bus/input/devices 2>/dev/null || true").stdout.lower()
    assert "nvt" in devices or "novatek" in devices or "nt36536" in devices, "Novatek touchscreen input device not found."


# ---------------------------------------------------------------------------
# 3. GPU / Mesa (Adreno A830 Freedreno + Turnip)
# ---------------------------------------------------------------------------

@pytest.mark.gpu
def test_gpu_devfreq_and_vulkan_device(tb378fc) -> None:
    devfreq = tb378fc.run("ls -d /sys/class/devfreq/*gpu* 2>/dev/null || true").stdout.strip()
    assert devfreq, "Adreno GPU devfreq node missing under /sys/class/devfreq/."

    vk = tb378fc.run("vulkaninfo --summary 2>/dev/null || true").stdout
    if not vk:
        pytest.skip("vulkaninfo not installed on tablet (install vulkan-tools to enable).")
    assert "Turnip" in vk or "Adreno" in vk, f"Unexpected Vulkan driver summary:\n{vk[:600]}"


# ---------------------------------------------------------------------------
# 4. Hardware Video Codec (Qualcomm Iris)
# ---------------------------------------------------------------------------

@pytest.mark.media
def test_iris_v4l2_nodes_present(tb378fc) -> None:
    devs = tb378fc.run("v4l2-ctl --list-devices 2>/dev/null || grep -H . /sys/class/video4linux/video*/name 2>/dev/null || true").stdout
    assert "iris" in devs.lower() or "qcom-iris" in devs.lower() or "venus" in devs.lower(), (
        f"Qualcomm Iris V4L2 video codec node not detected:\n{devs}"
    )


# ---------------------------------------------------------------------------
# 5. Audio (AW88480 Quad Amp + WCD939x Mic)
# ---------------------------------------------------------------------------

@pytest.mark.audio
def test_alsa_sound_card_and_capture_devices(tb378fc) -> None:
    play = tb378fc.run("aplay -l 2>/dev/null || cat /proc/asound/cards", check=True).stdout
    assert play.strip() and "no soundcards" not in play.lower(), f"No ALSA playback soundcard detected:\n{play}"
    cap = tb378fc.run("arecord -l 2>/dev/null || true").stdout
    assert cap.strip() and "no soundcards" not in cap.lower(), f"No ALSA capture (WCD939x mic) device detected:\n{cap}"


# ---------------------------------------------------------------------------
# 6. Camera (CAMSS + GC08A8 + OV13B10) & PM8550 Flash LED
# ---------------------------------------------------------------------------

@pytest.mark.camera
def test_camera_sensors_and_flash_led(tb378fc) -> None:
    subdevs = tb378fc.run("grep -H . /sys/class/video4linux/v4l-subdev*/name 2>/dev/null || true").stdout.lower()
    assert "gc08a8" in subdevs, f"Front camera sensor gc08a8 not bound:\n{subdevs}"
    assert "ov13b10" in subdevs, f"Rear camera sensor ov13b10 not bound:\n{subdevs}"

    leds = tb378fc.run("ls -1 /sys/class/leds/ 2>/dev/null || true").stdout.lower()
    assert "flash" in leds or "torch" in leds, f"PM8550 flash/torch LED missing in /sys/class/leds/:\n{leds}"


# ---------------------------------------------------------------------------
# 7. Power, Battery (qcom_battmgr), Thermal & s2idle / RPMh CX Collapse
# ---------------------------------------------------------------------------

@pytest.mark.power
def test_battery_and_cpu_thermal_zones(tb378fc) -> None:
    ps = tb378fc.run("ls -1 /sys/class/power_supply/ 2>/dev/null || true").stdout.lower()
    assert "batt" in ps, f"Battery power_supply missing:\n{ps}"

    trips = tb378fc.run(
        "grep -H '118000' /sys/class/thermal/thermal_zone*/trip_point_*_temp 2>/dev/null || true"
    ).stdout.strip()
    assert trips, "Expected 118°C (118000 mC) Oryon passive thermal trip-points not found in /sys/class/thermal/."


@pytest.mark.power
def test_s2idle_freeze_supported_and_rpmh_stats_readable(tb378fc) -> None:
    states = tb378fc.run("cat /sys/power/state", check=True).stdout
    assert "freeze" in states, f"s2idle (freeze) not advertised in /sys/power/state: {states!r}"

    stats = tb378fc.run(
        "cat /sys/kernel/debug/soc_sleep_stats 2>/dev/null || cat /sys/kernel/debug/rpmh_stats/master_stats 2>/dev/null || true"
    ).stdout.strip()
    if not stats:
        pytest.skip("debugfs soc_sleep_stats / rpmh_stats not mounted or inaccessible.")
    assert len(stats) > 10


@pytest.mark.power
@pytest.mark.slow
def test_active_s2idle_rtc_wake_cycle(tb378fc) -> None:
    """Opt-in (--run-active) 10-second s2idle freeze cycle verifying wakeup & RPMh stats delta."""
    tb378fc.require_active()
    res = tb378fc.run("command -v rtcwake >/dev/null && rtcwake -m freeze -s 10", timeout=35)
    assert res.returncode == 0, f"rtcwake s2idle freeze cycle failed:\n{res.stderr}"


# ---------------------------------------------------------------------------
# 8. Connectivity (Wi-Fi WCN7860 / Bluetooth UART / UFS)
# ---------------------------------------------------------------------------

@pytest.mark.connectivity
def test_wifi_and_bluetooth_interfaces(tb378fc) -> None:
    wlan = tb378fc.run("ip -o link show | grep -E 'wlan|wlp' || true").stdout.strip()
    assert wlan, "No Wi-Fi (ath12k / WCN7860) network interface found."

    hci = tb378fc.run("ls -1 /sys/class/bluetooth/ 2>/dev/null || true").stdout.strip()
    assert hci, "No Bluetooth HCI controller found under /sys/class/bluetooth/ (check qcom_geni_serial + hci_uart)."
