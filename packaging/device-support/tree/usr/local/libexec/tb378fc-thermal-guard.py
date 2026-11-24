#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""TB378FC Native 3-Profile Power-Profiles D-Bus Service, uclamp/cpuset Scheduler,
Dynamic Thermal Wall (118C / 85C / 75C), 6W Total Battery Power Cap, and Iris Media Controller.

Replaces x86/placeholder power-profiles-daemon on Lenovo Xiaoxin Pad Pro GT 13
(SM8750P) by exporting org.freedesktop.UPower.PowerProfiles and
net.hadess.PowerProfiles on the system bus with all three native GNOME modes:
  - performance: uncapped 8-core full OPP frequencies, 118C hardware-safe thermal
    ceiling, 2ms schedutil response, while keeping EAS/cpuidle/uclamp scheduling on.
  - balanced: lifted caps (~2.92/3.28 GHz, 832 MHz GPU), 85C dynamic thermal wall
    with gentle 75/80/85C steps, 4ms schedutil, Iris VPU35 media step-down, and
    uclamp/cpuset foreground-background scheduling.
  - power-saver: inherits conservative caps (<=2.23/2.44 GHz, <=660 MHz GPU),
    75C thermal wall, closed-loop <=6.0W total battery discharge power cap (with
    calibrated AC fallback), Iris media step-down, and tight uclamp scheduling.
"""
import glob
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
except ImportError:
    Gio = None
    GLib = None

STATE_FILE = Path("/var/lib/tb378fc/power-profile")
VALID_PROFILES = ("power-saver", "balanced", "performance")

# Fallback OPP ceilings (dynamically snapped to sysfs available + boost OPPs at startup)
DEFAULT_MAX_P0 = 3_532_800
DEFAULT_MAX_P6 = 4_320_000
DEFAULT_MAX_GPU = 1_100_000_000

# Profile baseline configuration:
#   caps: (policy0_khz, policy6_khz, gpu_hz)
#   rate_limit_us: schedutil rate_limit_us
#   passive_trip_mc: kernel CPU/GPU passive thermal trip point (mC)
#   passive_hyst_mc: kernel passive trip hysteresis (mC)
#   thermal_stages: list of (enter_mc, exit_mc, p0_khz, p6_khz, gpu_hz)
#   media_caps: optional (p0_khz, p6_khz, gpu_hz) when Iris VPU35 is actively decoding
#   uclamp: dict[slice_name -> (min_str, max_str)]
PROFILE_CONFIG = {
    "performance": {
        "caps": (DEFAULT_MAX_P0, DEFAULT_MAX_P6, DEFAULT_MAX_GPU),
        "rate_limit_us": 2_000,
        "passive_trip_mc": 118_000,
        "passive_hyst_mc": 8_000,
        "thermal_stages": [
            (0, 0, DEFAULT_MAX_P0, DEFAULT_MAX_P6, DEFAULT_MAX_GPU),
        ],
        "media_caps": None,
        "uclamp": {
            "session.slice": ("12.00", "max"),
            "app.slice": ("0.00", "max"),
            "background.slice": ("0.00", "35.00"),
        },
    },
    "balanced": {
        "caps": (2_918_400, 3_283_200, 832_000_000),
        "rate_limit_us": 4_000,
        "passive_trip_mc": 85_000,
        "passive_hyst_mc": 5_000,
        "thermal_stages": [
            (0, 0, 2_918_400, 3_283_200, 832_000_000),
            (75_000, 70_000, 2_400_000, 2_649_600, 734_000_000),
            (80_000, 75_000, 1_996_800, 2_246_400, 660_000_000),
            (85_000, 80_000, 1_785_600, 1_958_400, 525_000_000),
        ],
        "media_caps": (2_227_200, 2_246_400, 607_000_000),
        "uclamp": {
            "session.slice": ("12.00", "max"),
            "app.slice": ("0.00", "80.00"),
            "background.slice": ("0.00", "30.00"),
        },
    },
    "power-saver": {
        "caps": (2_227_200, 2_438_400, 660_000_000),
        "rate_limit_us": 12_000,
        "passive_trip_mc": 75_000,
        "passive_hyst_mc": 5_000,
        "thermal_stages": [
            (0, 0, 2_227_200, 2_438_400, 660_000_000),
            (65_000, 60_000, 1_785_600, 1_958_400, 525_000_000),
            (70_000, 65_000, 1_555_200, 1_689_600, 443_000_000),
            (75_000, 70_000, 1_152_000, 1_209_600, 389_000_000),
        ],
        "media_caps": (1_555_200, 1_401_600, 443_000_000),
        "uclamp": {
            "session.slice": ("10.00", "max"),
            "app.slice": ("0.00", "50.00"),
            "background.slice": ("0.00", "25.00"),
        },
    },
}

# Closed-loop 6.0 W total battery discharge power-cap ladder for power-saver
POWER_CAP_TARGET_W = 6.0
POWER_CAP_HARD_W = 7.5
POWER_CAP_RECOVER_W = 5.2
POWER_CAP_RECOVER_TICKS = 3
POWER_CAP_EMA_ALPHA = 0.5
POWER_CAP_AC_FALLBACK_LEVEL = 2  # ~6W calibrated cap when on USB/AC charger

POWER_SAVER_POWER_STEPS = [
    (2_227_200, 2_438_400, 660_000_000),
    (1_996_800, 1_958_400, 607_000_000),
    (1_785_600, 1_689_600, 525_000_000),
    (1_555_200, 1_401_600, 443_000_000),
    (1_363_200, 1_209_600, 389_000_000),
    (1_152_000, 1_017_600, 342_000_000),
]

PPD_XML_TEMPLATE = """
<node>
  <interface name="{iface}">
    <method name="HoldProfile">
      <arg type="s" name="profile" direction="in"/>
      <arg type="s" name="reason" direction="in"/>
      <arg type="s" name="application_id" direction="in"/>
      <arg type="u" name="cookie" direction="out"/>
    </method>
    <method name="ReleaseProfile">
      <arg type="u" name="cookie" direction="in"/>
    </method>
    <method name="SetActionEnabled">
      <arg type="s" name="action" direction="in"/>
      <arg type="b" name="enabled" direction="in"/>
    </method>
    <signal name="ProfileReleased">
      <arg type="u" name="cookie"/>
    </signal>
    <property type="s" name="ActiveProfile" access="readwrite"/>
    <property type="s" name="PerformanceInhibited" access="read"/>
    <property type="s" name="PerformanceDegraded" access="read"/>
    <property type="aa{{sv}}" name="Profiles" access="read"/>
    <property type="as" name="Actions" access="read"/>
    <property type="aa{{sv}}" name="ActionsInfo" access="read"/>
    <property type="aa{{sv}}" name="ActiveProfileHolds" access="read"/>
    <property type="b" name="BatteryAware" access="readwrite"/>
    <property type="s" name="Version" access="read"/>
  </interface>
</node>
"""


def write_sysfs(path: Path, value: str) -> bool:
    try:
        path.write_text(value + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_opp_table(path: Path) -> list[int]:
    try:
        vals = sorted({int(tok) for tok in path.read_text(encoding="utf-8").split() if tok.isdigit()})
        return vals
    except OSError:
        return []


def read_policy_opps(policy_dir: Path) -> list[int]:
    """Return sorted union of scaling_available_frequencies and scaling_boost_frequencies."""
    opps = set(read_opp_table(policy_dir / "scaling_available_frequencies"))
    opps.update(read_opp_table(policy_dir / "scaling_boost_frequencies"))
    return sorted(opps)


def read_ac_online(
    usb_psy_dir: Path = Path("/sys/class/power_supply/qcom-battmgr-usb"),
    bat_psy_dir: Path = Path("/sys/class/power_supply/qcom-battmgr-bat"),
) -> bool | None:
    """Return True when external USB/AC power is online, False on battery, or None if unavailable."""
    online = read_int(usb_psy_dir / "online")
    if online is not None:
        return online > 0
    try:
        status = (bat_psy_dir / "status").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if status in ("Charging", "Full", "Not charging"):
        return True
    if status == "Discharging":
        return False
    return None


def sync_wifi_power_save(enable_ps: bool, net_root: Path = Path("/sys/class/net")) -> int:
    """Toggle 802.11 power_save across dynamic wireless interfaces (e.g. wlp1s0, never hardcoded wlan0)."""
    iw_bin = shutil.which("iw")
    if not iw_bin or not net_root.is_dir():
        return 0
    mode = "on" if enable_ps else "off"
    updated = 0
    for iface_dir in sorted(net_root.iterdir()):
        if not (iface_dir / "wireless").is_dir():
            continue
        res = subprocess.run(
            [iw_bin, "dev", iface_dir.name, "set", "power_save", mode],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res.returncode == 0:
            updated += 1
    return updated


def snap_opp(target: int, opps: list[int]) -> int:
    if not opps:
        return target
    candidates = [f for f in opps if f <= target]
    return candidates[-1] if candidates else opps[0]


def next_stage(cur_stage: int, max_temp_mc: int, stages: list[tuple[int, int, int, int, int]]) -> int:
    if not stages:
        return 0
    if cur_stage >= len(stages):
        cur_stage = len(stages) - 1
    target = cur_stage
    for idx in range(cur_stage + 1, len(stages)):
        if max_temp_mc >= stages[idx][0]:
            target = idx
    if target != cur_stage:
        return target
    while target > 0 and max_temp_mc < stages[target][1]:
        target -= 1
    return target


def read_battery_discharge_watts(psy_dir: Path = Path("/sys/class/power_supply/qcom-battmgr-bat")) -> tuple[str, float | None]:
    """Return (status, discharge_watts_or_None) from qcom_battmgr battery sysfs."""
    try:
        status = (psy_dir / "status").read_text(encoding="utf-8").strip()
    except OSError:
        return ("Unknown", None)
    if status != "Discharging":
        return (status, None)
    p_now = read_int(psy_dir / "power_now")
    if p_now is not None and p_now != 0:
        return (status, abs(p_now) / 1_000_000.0)
    v_uv = read_int(psy_dir / "voltage_now")
    i_ua = read_int(psy_dir / "current_now")
    if v_uv is None or i_ua is None:
        return (status, None)
    return (status, abs(v_uv * i_ua) / 1_000_000_000_000.0)


class PowerCapController:
    """Closed-loop 6.0W total battery power cap controller with hysteresis."""

    def __init__(self, target_w: float = POWER_CAP_TARGET_W, recover_w: float = POWER_CAP_RECOVER_W):
        self.target_w = target_w
        self.recover_w = recover_w
        self.ema_w: float | None = None
        self.level = 0
        self.below_ticks = 0

    def reset(self) -> None:
        self.ema_w = None
        self.level = 0
        self.below_ticks = 0

    def update(self, status: str, watts: float | None) -> int:
        max_level = len(POWER_SAVER_POWER_STEPS) - 1
        if status != "Discharging" or watts is None:
            self.ema_w = None
            self.below_ticks = 0
            self.level = POWER_CAP_AC_FALLBACK_LEVEL
            return self.level

        if self.ema_w is None:
            self.ema_w = watts
        else:
            self.ema_w = POWER_CAP_EMA_ALPHA * watts + (1.0 - POWER_CAP_EMA_ALPHA) * self.ema_w

        if self.ema_w > POWER_CAP_HARD_W:
            self.level = min(max_level, self.level + 2)
            self.below_ticks = 0
        elif self.ema_w > self.target_w:
            self.level = min(max_level, self.level + 1)
            self.below_ticks = 0
        elif self.ema_w < self.recover_w:
            self.below_ticks += 1
            if self.below_ticks >= POWER_CAP_RECOVER_TICKS and self.level > 0:
                self.level -= 1
                self.below_ticks = 0
        else:
            self.below_ticks = 0
        return self.level


class IrisMediaDetector:
    """Detects sustained Iris VPU35 hardware decode activity from runtime_active_time."""

    def __init__(self, stat_path: Path = Path("/sys/class/video4linux/video0/device/power/runtime_active_time")):
        self.stat_path = stat_path
        self.last_ms: int | None = None
        self.active_streak = 0
        self.idle_streak = 0
        self.media_active = False

    def poll(self) -> bool:
        cur = read_int(self.stat_path)
        if cur is None:
            return False
        if self.last_ms is None:
            self.last_ms = cur
            return self.media_active
        delta = cur - self.last_ms
        self.last_ms = cur
        if delta >= 40:
            self.active_streak += 1
            self.idle_streak = 0
            if self.active_streak >= 2:
                self.media_active = True
        else:
            self.idle_streak += 1
            self.active_streak = 0
            if self.idle_streak >= 3:
                self.media_active = False
        return self.media_active


class ProfileState:
    """Tracks base profile, HoldProfile cookies, and effective active profile."""

    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self.base_profile = self._load_base_profile()
        self.battery_aware = True
        self.holds: dict[int, dict[str, str]] = {}
        self._next_cookie = 1

    def _load_base_profile(self) -> str:
        try:
            saved = self.state_file.read_text(encoding="utf-8").strip()
            if saved in VALID_PROFILES:
                return saved
        except OSError:
            pass
        return "balanced"

    def _save_base_profile(self, profile: str) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(profile + "\n", encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    @property
    def active_profile(self) -> str:
        if not self.holds:
            return self.base_profile
        # Match power-profiles-daemon precedence: performance hold beats power-saver hold
        held = list(self.holds.values())
        for h in held:
            if h["profile"] == "performance":
                return "performance"
        return held[-1]["profile"]

    def set_active_profile(self, profile: str) -> list[int]:
        if profile not in VALID_PROFILES:
            raise ValueError(f"Unsupported profile: {profile}")
        released = list(self.holds.keys())
        self.holds.clear()
        self.base_profile = profile
        self._save_base_profile(profile)
        return released

    def hold_profile(self, profile: str, reason: str, app_id: str, sender: str) -> int:
        if profile not in VALID_PROFILES:
            raise ValueError(f"Unsupported profile: {profile}")
        cookie = self._next_cookie
        self._next_cookie += 1
        self.holds[cookie] = {
            "profile": profile,
            "reason": reason,
            "application_id": app_id,
            "sender": sender,
        }
        return cookie

    def release_profile(self, cookie: int) -> bool:
        return self.holds.pop(cookie, None) is not None

    def release_sender(self, sender: str) -> list[int]:
        released = [c for c, h in self.holds.items() if h.get("sender") == sender]
        for c in released:
            self.holds.pop(c, None)
        return released

    def apply_power_source(self, ac_online: bool) -> bool:
        """Auto-switch base profile on AC/battery transitions when battery_aware is enabled.
        - Plugged in (AC online): promote base profile to 'performance'.
        - Unplugged (on battery): step down 'performance' -> 'balanced', preserving manual 'power-saver'.
        """
        if not self.battery_aware:
            return False
        if ac_online:
            if self.base_profile != "performance":
                self.base_profile = "performance"
                self._save_base_profile("performance")
                return True
            return False
        if self.base_profile == "performance":
            self.base_profile = "balanced"
            self._save_base_profile("balanced")
            return True
        return False


def _enable_cpuset_subtree(cgroup_dir: Path) -> None:
    ctrl = cgroup_dir / "cgroup.controllers"
    sub = cgroup_dir / "cgroup.subtree_control"
    try:
        if "cpuset" in ctrl.read_text(encoding="utf-8").split():
            if "cpuset" not in sub.read_text(encoding="utf-8").split():
                write_sysfs(sub, "+cpuset")
    except OSError:
        pass


def apply_user_slice_uclamp(uclamp_cfg: dict[str, tuple[str, str]], cgroup_root: Path = Path("/sys/fs/cgroup")) -> int:
    """Write cpu.uclamp.{min,max} and background.slice cpuset (0-5) to active user slices (zero hardcoded UID)."""
    updated = 0
    _enable_cpuset_subtree(cgroup_root)
    _enable_cpuset_subtree(cgroup_root / "user.slice")
    for user_svc in sorted(cgroup_root.glob("user.slice/user-*.slice/user@*.service")):
        _enable_cpuset_subtree(user_svc.parent)
        _enable_cpuset_subtree(user_svc)
        for slice_name, (umin, umax) in uclamp_cfg.items():
            sdir = user_svc / slice_name
            if not sdir.is_dir():
                continue
            ok1 = write_sysfs(sdir / "cpu.uclamp.min", umin)
            ok2 = write_sysfs(sdir / "cpu.uclamp.max", umax)
            if slice_name == "background.slice" and (sdir / "cpuset.cpus").exists():
                write_sysfs(sdir / "cpuset.cpus", "0-5")
            if ok1 or ok2:
                updated += 1
    return updated



CONSOLIDATED_MARKER = Path("/run/tb378fc-system-consolidated")
POWER_KEY_SCRIPT = Path("/usr/local/libexec/tb378fc-power-key.py")
VOLUME_ORIENT_SCRIPT = Path("/usr/local/libexec/tb378fc-volume-orientation")


class ZramRecompressor:
    """Periodic cold-page zram secondary (lz4 -> zstd) recompression & compaction.

    Uses Linux 7.4 CONFIG_ZRAM_MULTI_COMP sysfs interface without spawning
    subprocesses:
      1. Marks resident zram pages idle via /sys/block/zram0/idle ('all').
      2. On the next interval (120s later), any pages that remained untouched
         are recompressed to secondary zstd in bounded batches (max_pages=4096
         for normal idle pages, max_pages=1024 for huge_idle pages), followed
         by zsmalloc pool compaction (/sys/block/zram0/compact).
    """

    def __init__(
        self,
        zram_dir: Path = Path("/sys/block/zram0"),
        interval_sec: float = 120.0,
        min_orig_bytes: int = 8 * 1024 * 1024,
    ):
        self.zram_dir = zram_dir
        self.interval_sec = interval_sec
        self.min_orig_bytes = min_orig_bytes
        self.last_check = 0.0
        self.idle_marked = False

    def poll(self, now_mono: float | None = None) -> bool:
        if now_mono is None:
            now_mono = time.monotonic()
        if self.last_check and (now_mono - self.last_check) < self.interval_sec:
            return False
        self.last_check = now_mono

        mm_stat = self.zram_dir / "mm_stat"
        idle_path = self.zram_dir / "idle"
        recomp_path = self.zram_dir / "recompress"
        compact_path = self.zram_dir / "compact"
        if not (mm_stat.is_file() and idle_path.exists() and recomp_path.exists()):
            return False

        try:
            fields = mm_stat.read_text(encoding="utf-8").split()
            orig_bytes = int(fields[0]) if fields else 0
        except (OSError, ValueError):
            return False

        if orig_bytes < self.min_orig_bytes:
            self.idle_marked = False
            return False

        did_recomp = False
        if self.idle_marked:
            write_sysfs(recomp_path, "type=idle max_pages=4096")
            write_sysfs(recomp_path, "type=huge_idle max_pages=1024")
            if compact_path.exists():
                write_sysfs(compact_path, "1")
            did_recomp = True

        if write_sysfs(idle_path, "all"):
            self.idle_marked = True
        return did_recomp


def _load_companion_module(mod_name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(mod_name, str(path))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ThermalGuardDaemon:
    def __init__(self):
        self.state = ProfileState()
        self.power_ctrl = PowerCapController()
        self.media_det = IrisMediaDetector()
        self.zram_recomp = ZramRecompressor()
        self.companion_stop = threading.Event()
        self.companion_threads: list[threading.Thread] = []

        self.boost_path = Path("/sys/devices/system/cpu/cpufreq/boost")
        self.p0_max_path = Path("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq")
        self.p6_max_path = Path("/sys/devices/system/cpu/cpufreq/policy6/scaling_max_freq")
        self.gpu_max_path = Path("/sys/class/devfreq/3d00000.gpu/max_freq")

        self.p0_opps = read_policy_opps(Path("/sys/devices/system/cpu/cpufreq/policy0"))
        self.p6_opps = read_policy_opps(Path("/sys/devices/system/cpu/cpufreq/policy6"))
        self.gpu_opps = read_opp_table(Path("/sys/class/devfreq/3d00000.gpu/available_frequencies"))

        # Patch performance ceilings to exact hardware max OPP discovered at boot
        perf_p0 = self.p0_opps[-1] if self.p0_opps else DEFAULT_MAX_P0
        perf_p6 = self.p6_opps[-1] if self.p6_opps else DEFAULT_MAX_P6
        perf_gpu = self.gpu_opps[-1] if self.gpu_opps else DEFAULT_MAX_GPU
        PROFILE_CONFIG["performance"]["caps"] = (perf_p0, perf_p6, perf_gpu)
        PROFILE_CONFIG["performance"]["thermal_stages"] = [(0, 0, perf_p0, perf_p6, perf_gpu)]

        self.cpu_temp_paths, self.passive_trips = self._discover_thermal_zones()
        self.stage = 0
        self.last_profile: str | None = None
        self.last_passive_mc: int | None = None
        self.last_applied: tuple[int, int, int, int, int] | None = None
        self.last_ac_online: bool | None = None
        self.last_wifi_ps: bool | None = None
        self.last_uclamp_profile: str | None = None
        self.last_uclamp_refresh = 0.0
        self.perf_degraded = ""

        self.bus = None
        self.loop = None
        self.reg_ids: list[int] = []
        self.owner_ids: list[int] = []

    def _discover_thermal_zones(self) -> tuple[list[Path], list[tuple[Path, Path]]]:
        cpu_temps: list[Path] = []
        passive_trips: list[tuple[Path, Path]] = []
        for zdir in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            try:
                ztype = (zdir / "type").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            is_cpu = ztype.startswith(("cpu-", "cpuss-", "cpu_", "cpuss_"))
            is_gpu = ztype.startswith("gpuss")
            if is_cpu and (zdir / "temp").exists():
                cpu_temps.append(zdir / "temp")
            if not (is_cpu or is_gpu):
                continue
            for ttype_path in sorted(zdir.glob("trip_point_*_type")):
                try:
                    ttype = ttype_path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if ttype != "passive":
                    continue
                prefix = ttype_path.name[:-5]
                passive_trips.append((zdir / f"{prefix}_temp", zdir / f"{prefix}_hyst"))
        print(
            f"tb378fc-thermal-guard: discovered {len(cpu_temps)} CPU sensors, "
            f"{len(passive_trips)} passive trips; OPPs p0={len(self.p0_opps)} p6={len(self.p6_opps)} gpu={len(self.gpu_opps)}",
            flush=True,
        )
        return cpu_temps, passive_trips

    def _apply_kernel_passive_trips(self, temp_mc: int, hyst_mc: int) -> None:
        if self.last_passive_mc == temp_mc:
            return
        updated = 0
        for temp_path, hyst_path in self.passive_trips:
            if write_sysfs(temp_path, str(temp_mc)):
                updated += 1
            write_sysfs(hyst_path, str(hyst_mc))
        self.last_passive_mc = temp_mc
        print(
            f"tb378fc-thermal-guard: updated {updated} kernel passive trips to {temp_mc // 1000}C (hyst {hyst_mc // 1000}C)",
            flush=True,
        )

    def _set_schedutil_rate_limit(self, rate_us: int) -> None:
        for rl in glob.glob("/sys/devices/system/cpu/cpufreq/policy*/schedutil/rate_limit_us"):
            write_sysfs(Path(rl), str(rate_us))

    def _read_max_cpu_temp(self) -> int:
        max_mc = 0
        for tp in self.cpu_temp_paths:
            val = read_int(tp)
            if val is not None and val > max_mc:
                max_mc = val
        return max_mc

    def _profiles_variant(self):
        items = []
        for prof in VALID_PROFILES:
            items.append({
                "Profile": GLib.Variant("s", prof),
                "PlatformDriver": GLib.Variant("s", "tb378fc"),
                "CpuDriver": GLib.Variant("s", "qcom-cpufreq-hw"),
                "Driver": GLib.Variant("s", "tb378fc"),
            })
        return GLib.Variant("aa{sv}", items)

    def _holds_variant(self):
        items = []
        for h in self.state.holds.values():
            items.append({
                "ApplicationId": GLib.Variant("s", h["application_id"]),
                "Profile": GLib.Variant("s", h["profile"]),
                "Reason": GLib.Variant("s", h["reason"]),
            })
        return GLib.Variant("aa{sv}", items)

    def _get_property_variant(self, prop_name: str):
        if prop_name == "ActiveProfile":
            return GLib.Variant("s", self.state.active_profile)
        if prop_name == "PerformanceInhibited":
            return GLib.Variant("s", "")
        if prop_name == "PerformanceDegraded":
            return GLib.Variant("s", self.perf_degraded)
        if prop_name == "Profiles":
            return self._profiles_variant()
        if prop_name == "Actions":
            return GLib.Variant("as", [])
        if prop_name == "ActionsInfo":
            return GLib.Variant("aa{sv}", [])
        if prop_name == "ActiveProfileHolds":
            return self._holds_variant()
        if prop_name == "BatteryAware":
            return GLib.Variant("b", self.state.battery_aware)
        if prop_name == "Version":
            return GLib.Variant("s", "0.30")
        return None

    def _emit_props_changed(self, changed: dict[str, object]) -> None:
        if self.bus is None or not changed:
            return
        for obj_path, iface in (
            ("/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles"),
            ("/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
        ):
            try:
                self.bus.emit_signal(
                    None,
                    obj_path,
                    "org.freedesktop.DBus.Properties",
                    "PropertiesChanged",
                    GLib.Variant("(sa{sv}as)", (iface, changed, [])),
                )
            except Exception:
                pass

    def _emit_profile_released(self, cookie: int) -> None:
        if self.bus is None:
            return
        for obj_path, iface in (
            ("/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles"),
            ("/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
        ):
            try:
                self.bus.emit_signal(
                    None,
                    obj_path,
                    iface,
                    "ProfileReleased",
                    GLib.Variant("(u)", (cookie,)),
                )
            except Exception:
                pass

    def _on_method_call(self, _conn, sender, _obj_path, _iface, method_name, parameters, invocation):
        try:
            if method_name == "HoldProfile":
                profile, reason, app_id = parameters.unpack()
                old_active = self.state.active_profile
                cookie = self.state.hold_profile(profile, reason, app_id, sender)
                changed = {"ActiveProfileHolds": self._holds_variant()}
                if self.state.active_profile != old_active:
                    changed["ActiveProfile"] = GLib.Variant("s", self.state.active_profile)
                self._emit_props_changed(changed)
                self.tick()
                invocation.return_value(GLib.Variant("(u)", (cookie,)))
                return
            if method_name == "ReleaseProfile":
                (cookie,) = parameters.unpack()
                old_active = self.state.active_profile
                if not self.state.release_profile(cookie):
                    invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", f"Unknown cookie {cookie}")
                    return
                self._emit_profile_released(cookie)
                changed = {"ActiveProfileHolds": self._holds_variant()}
                if self.state.active_profile != old_active:
                    changed["ActiveProfile"] = GLib.Variant("s", self.state.active_profile)
                self._emit_props_changed(changed)
                self.tick()
                invocation.return_value(None)
                return
            if method_name == "SetActionEnabled":
                invocation.return_value(None)
                return
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", f"Unknown method {method_name}")
        except ValueError as exc:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", str(exc))
        except Exception as exc:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _on_get_property(self, _conn, _sender, _obj_path, _iface, prop_name):
        return self._get_property_variant(prop_name)

    def _on_set_property(self, _conn, _sender, _obj_path, _iface, prop_name, value):
        if prop_name == "ActiveProfile":
            new_prof = value.unpack()
            if new_prof not in VALID_PROFILES:
                return False
            old_active = self.state.active_profile
            released = self.state.set_active_profile(new_prof)
            for cookie in released:
                self._emit_profile_released(cookie)
            changed = {}
            if released:
                changed["ActiveProfileHolds"] = self._holds_variant()
            if self.state.active_profile != old_active or released:
                changed["ActiveProfile"] = GLib.Variant("s", self.state.active_profile)
            self._emit_props_changed(changed)
            print(f"tb378fc-thermal-guard: profile switched via D-Bus -> {self.state.active_profile}", flush=True)
            self.tick()
            return True
        if prop_name == "BatteryAware":
            self.state.battery_aware = bool(value.unpack())
            self._emit_props_changed({"BatteryAware": GLib.Variant("b", self.state.battery_aware)})
            return True
        return False

    def _on_name_owner_changed(self, _conn, _sender, _path, _iface, _sig, params):
        name, _old_owner, new_owner = params.unpack()
        if new_owner:
            return
        old_active = self.state.active_profile
        released = self.state.release_sender(name)
        if not released:
            return
        for cookie in released:
            self._emit_profile_released(cookie)
        changed = {"ActiveProfileHolds": self._holds_variant()}
        if self.state.active_profile != old_active:
            changed["ActiveProfile"] = GLib.Variant("s", self.state.active_profile)
        self._emit_props_changed(changed)
        self.tick()

    def setup_dbus(self) -> None:
        if Gio is None or GLib is None:
            print("tb378fc-thermal-guard: PyGObject Gio unavailable; running headless without D-Bus", flush=True)
            return
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        for obj_path, iface in (
            ("/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles"),
            ("/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
        ):
            node_info = Gio.DBusNodeInfo.new_for_xml(PPD_XML_TEMPLATE.format(iface=iface))
            reg_id = self.bus.register_object(
                obj_path,
                node_info.interfaces[0],
                self._on_method_call,
                self._on_get_property,
                self._on_set_property,
            )
            self.reg_ids.append(reg_id)
            own_id = Gio.bus_own_name_on_connection(
                self.bus,
                iface,
                Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE,
                None,
                None,
            )
            self.owner_ids.append(own_id)
        self.bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed,
        )
        print(
            f"tb378fc-thermal-guard: registered D-Bus PowerProfiles (active={self.state.active_profile})",
            flush=True,
        )

    def tick(self) -> bool:
        ac_online = read_ac_online()
        if ac_online is not None and ac_online != self.last_ac_online:
            old_active = self.state.active_profile
            self.last_ac_online = ac_online
            if self.state.apply_power_source(ac_online):
                new_active = self.state.active_profile
                print(
                    f"tb378fc-thermal-guard: power source {'AC' if ac_online else 'battery'} -> base profile {self.state.base_profile}",
                    flush=True,
                )
                if new_active != old_active and GLib is not None:
                    self._emit_props_changed({"ActiveProfile": GLib.Variant("s", new_active)})

        profile = self.state.active_profile
        cfg = PROFILE_CONFIG[profile]

        if ac_online is not None:
            want_wifi_ps = (not ac_online) and (profile != "performance")
            if want_wifi_ps != self.last_wifi_ps:
                sync_wifi_power_save(want_wifi_ps)
                self.last_wifi_ps = want_wifi_ps

        if profile != self.last_profile:
            self.stage = 0
            self.power_ctrl.reset()
            self._apply_kernel_passive_trips(cfg["passive_trip_mc"], cfg["passive_hyst_mc"])
            self.last_profile = profile

        now = time.monotonic()
        if self.last_uclamp_profile != profile or (now - self.last_uclamp_refresh) >= 15.0:
            apply_user_slice_uclamp(cfg["uclamp"])
            self.last_uclamp_profile = profile
            self.last_uclamp_refresh = now

        self.zram_recomp.poll(now)

        max_mc = self._read_max_cpu_temp()
        stages = cfg["thermal_stages"]
        new_stage = next_stage(self.stage, max_mc, stages)
        if new_stage != self.stage:
            print(
                f"tb378fc-thermal-guard: thermal stage {self.stage} -> {new_stage} "
                f"(max_cpu={max_mc / 1000.0:.1f}C, profile={profile})",
                flush=True,
            )
            self.stage = new_stage

        prof_p0, prof_p6, prof_gpu = cfg["caps"]
        _, _, th_p0, th_p6, th_gpu = stages[self.stage]
        eff_p0 = min(prof_p0, th_p0)
        eff_p6 = min(prof_p6, th_p6)
        eff_gpu = min(prof_gpu, th_gpu)
        eff_rl = cfg["rate_limit_us"]
        if profile != "performance" and self.stage >= 2:
            eff_rl = max(eff_rl, 15_000)

        # Iris VPU35 media decode step-down (balanced & power-saver)
        media_active = self.media_det.poll()
        media_caps = cfg.get("media_caps")
        if media_active and media_caps is not None:
            eff_p0 = min(eff_p0, media_caps[0])
            eff_p6 = min(eff_p6, media_caps[1])
            eff_gpu = min(eff_gpu, media_caps[2])

        # Closed-loop 6.0W total battery power cap in power-saver mode
        pwr_info = ""
        if profile == "power-saver":
            b_status, b_watts = read_battery_discharge_watts()
            p_level = self.power_ctrl.update(b_status, b_watts)
            pw_p0, pw_p6, pw_gpu = POWER_SAVER_POWER_STEPS[p_level]
            eff_p0 = min(eff_p0, pw_p0)
            eff_p6 = min(eff_p6, pw_p6)
            eff_gpu = min(eff_gpu, pw_gpu)
            ema_str = f"{self.power_ctrl.ema_w:.2f}W" if self.power_ctrl.ema_w is not None else b_status
            pwr_info = f" pwr={ema_str}(lvl={p_level})"

        eff_p0 = snap_opp(eff_p0, self.p0_opps)
        eff_p6 = snap_opp(eff_p6, self.p6_opps)
        eff_gpu = snap_opp(eff_gpu, self.gpu_opps)
        eff_boost = 1 if profile == "performance" else 0
        target = (eff_p0, eff_p6, eff_gpu, eff_rl, eff_boost)

        if target != self.last_applied:
            if eff_boost and self.boost_path.exists():
                write_sysfs(self.boost_path, "1")
            write_sysfs(self.p0_max_path, str(target[0]))
            write_sysfs(self.p6_max_path, str(target[1]))
            if not eff_boost and self.boost_path.exists():
                write_sysfs(self.boost_path, "0")
            if self.gpu_max_path.exists():
                write_sysfs(self.gpu_max_path, str(target[2]))
            self._set_schedutil_rate_limit(target[3])
            print(
                f"tb378fc-thermal-guard: applied policy0={target[0]}kHz "
                f"policy6={target[1]}kHz gpu={target[2]}Hz rate_limit={target[3]}us boost={eff_boost} "
                f"(profile={profile}, stage={self.stage}, temp={max_mc / 1000.0:.1f}C, media={int(media_active)}{pwr_info})",
                flush=True,
            )
            self.last_applied = target

        return True

    def _start_companions(self) -> None:
        try:
            CONSOLIDATED_MARKER.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError:
            pass

        def _supervised(label: str, path: Path, entry_fn: str) -> None:
            try:
                mod = _load_companion_module(f"tb378fc_comp_{label.replace('-', '_')}", path)
                fn = getattr(mod, entry_fn)
            except Exception as exc:
                print(f"tb378fc-thermal-guard: failed to load {label}: {exc}", flush=True)
                return
            while not self.companion_stop.is_set():
                try:
                    fn(stop_event=self.companion_stop)
                except Exception as exc:
                    if not self.companion_stop.is_set():
                        print(f"tb378fc-thermal-guard: {label} worker retry after: {exc}", flush=True)
                        self.companion_stop.wait(2.0)

        for label, script_path, fn_name in (
            ("power-key", POWER_KEY_SCRIPT, "run_power_key_loop"),
            ("volume-orientation", VOLUME_ORIENT_SCRIPT, "run_volume_router_loop"),
        ):
            if script_path.is_file():
                t = threading.Thread(
                    target=_supervised,
                    args=(label, script_path, fn_name),
                    name=f"tb378fc-{label}",
                    daemon=True,
                )
                t.start()
                self.companion_threads.append(t)

    def run(self) -> int:
        self._start_companions()
        self.setup_dbus()
        self.tick()
        if GLib is not None:
            self.loop = GLib.MainLoop()

            def _stop(*_args):
                self.companion_stop.set()
                if self.loop is not None:
                    self.loop.quit()

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)
            GLib.timeout_add(1000, self.tick)
            try:
                self.loop.run()
            finally:
                self._cleanup()
        else:
            running = True

            def _stop_plain(*_args):
                nonlocal running
                running = False
                self.companion_stop.set()

            signal.signal(signal.SIGTERM, _stop_plain)
            signal.signal(signal.SIGINT, _stop_plain)
            try:
                while running:
                    time.sleep(1.0)
                    self.tick()
            finally:
                self._cleanup()
        return 0

    def _cleanup(self) -> None:
        self.companion_stop.set()
        for t in self.companion_threads:
            t.join(timeout=1.5)
        try:
            CONSOLIDATED_MARKER.unlink(missing_ok=True)
        except OSError:
            pass
        # Leave system in safe balanced defaults on service shutdown
        bal = PROFILE_CONFIG["balanced"]["caps"]
        write_sysfs(self.p0_max_path, str(snap_opp(bal[0], self.p0_opps)))
        write_sysfs(self.p6_max_path, str(snap_opp(bal[1], self.p6_opps)))
        if self.boost_path.exists():
            write_sysfs(self.boost_path, "0")
        if self.gpu_max_path.exists():
            write_sysfs(self.gpu_max_path, str(snap_opp(bal[2], self.gpu_opps)))


def main() -> int:
    return ThermalGuardDaemon().run()


if __name__ == "__main__":
    sys.exit(main())
