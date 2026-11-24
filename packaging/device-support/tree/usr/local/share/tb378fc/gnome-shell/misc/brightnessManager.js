// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Lenovo Xiaoxin Pad Pro GT 13 (TB378FC) GNOME 50 BrightnessManager overlay.
 *
 * Replaces gsd-power's broken single-point linear normalization (norm = 1.5 * lux)
 * and fixes GNOME 50's screen-rotation (_monitorsChanged) bias-ratchet bug by
 * driving hardware-calibrated auto-brightness directly inside the compositor:
 *  - Subscribes directly to net.hadess.SensorProxy physical SI lux (stk6b9x)
 *    when org.gnome.settings-daemon.plugins.power 'ambient-enabled' is true,
 *    ignoring gsd-power's relative SetAutoBrightnessTarget calls.
 *  - Maps physical SI lux [0..5500+ lx] onto TB378FC's dual AW99706B 12-bit
 *    linear-current backlight [40..4095] (~8..800 nits) via a deterministic
 *    log-interpolated perceptual LUT (booting in 0 lux or 5000 lux always
 *    yields identical, repeatable targets).
 *  - Applies Android-style asymmetric temporal debounce (1800 ms brightening
 *    hold to reject white-webpage reflections/glare, 900 ms darkening hold to
 *    ignore brief hand shadows) plus a self-driven 30 Hz smooth ramp timer
 *    that always converges all the way down to darkness even when SensorProxy
 *    stops emitting after ambient light stabilizes.
 *  - Keeps Quick Settings brightness slider synced to real screen brightness,
 *    records genuine user slider/hotkey adjustments as a tapered preference
 *    offset, and preserves brightness cleanly across 4-way screen rotations.
 */
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from '../ui/main.js';

const SCALE_VALUE_N_STEPS = 20;
const SCALE_VALUE_CHANGE_EPSILON = 0.001;

const KEYBINDING_SCHEMA = 'org.gnome.shell.keybindings';
const POWER_SCHEMA = 'org.gnome.settings-daemon.plugins.power';

const SENSOR_BUS_NAME = 'net.hadess.SensorProxy';
const SENSOR_OBJECT_PATH = '/net/hadess/SensorProxy';
const SENSOR_INTERFACE = 'net.hadess.SensorProxy';

// Calibrated physical SI lux -> AW99706B relative brightness [0..1] (40..4095 -> ~8..800 nits)
const TB378FC_ALS_LUT = [
    [0.0,    0.018], //    0 lx (pitch dark):   ~113/4095 (~22 nits night floor)
    [2.0,    0.035], //    2 lx (dark bedroom): ~182/4095 (~35 nits)
    [8.0,    0.072], //    8 lx (dim night):    ~332/4095 (~65 nits)
    [25.0,   0.128], //   25 lx (dim indoor):   ~559/4095 (~110 nits)
    [85.0,   0.220], //   85 lx (normal room):  ~932/4095 (~182 nits, matches 906 default)
    [250.0,  0.325], //  250 lx (bright room): ~1358/4095 (~265 nits)
    [600.0,  0.455], //  600 lx (window/office):~1885/4095 (~370 nits)
    [1500.0, 0.650], // 1500 lx (overcast/sill):~2676/4095 (~525 nits)
    [3200.0, 0.860], // 3200 lx (outdoors):    ~3527/4095 (~690 nits)
    [5500.0, 1.000], // 5500+ lx (sunlight):    4095/4095 (800 nits peak)
];

const BRIGHTEN_DEBOUNCE_MS = 1800;
const DARKEN_DEBOUNCE_MS = 900;
const ALS_TARGET_DEADBAND = 0.012;
const ANIM_INTERVAL_MS = 33;
const DARK_TAPER_KNEE = 0.22;

function clamp(val, lo, hi) {
    return Math.min(Math.max(val, lo), hi);
}

export function luxToRelativeBrightness(lux) {
    if (!Number.isFinite(lux) || lux <= TB378FC_ALS_LUT[0][0])
        return TB378FC_ALS_LUT[0][1];

    const last = TB378FC_ALS_LUT[TB378FC_ALS_LUT.length - 1];
    if (lux >= last[0])
        return last[1];

    const logLux = Math.log1p(lux);
    for (let i = 0; i < TB378FC_ALS_LUT.length - 1; i++) {
        const [x0, y0] = TB378FC_ALS_LUT[i];
        const [x1, y1] = TB378FC_ALS_LUT[i + 1];
        if (lux <= x1) {
            const l0 = Math.log1p(x0);
            const l1 = Math.log1p(x1);
            const t = l1 > l0 ? (logLux - l0) / (l1 - l0) : 0.0;
            return y0 + t * (y1 - y0);
        }
    }
    return last[1];
}

function biasTaper(base, bias) {
    if (bias > 0.0 && base < DARK_TAPER_KNEE)
        return 0.25 + 0.75 * Math.max(0.0, base / DARK_TAPER_KNEE);
    return 1.0;
}

export function applyUserBias(base, bias) {
    const t = biasTaper(base, bias);
    return clamp(base + bias * t, 0.0, 1.0);
}

export function computeUserBias(base, desiredBrightness) {
    const rawDiff = desiredBrightness - base;
    if (rawDiff > 0.0 && base < DARK_TAPER_KNEE) {
        const t = 0.25 + 0.75 * Math.max(0.0, base / DARK_TAPER_KNEE);
        return clamp(rawDiff / t, -1.0, 1.5);
    }
    return clamp(rawDiff, -1.0, 1.0);
}

export const BrightnessManager = GObject.registerClass({
    Signals: {
        'changed': {},
        'user-update': {},
    },
}, class BrightnessManager extends GObject.Object {
    constructor() {
        super();

        this._globalScale = null;
        this._monitorScales = new Map();

        this._powerSettings = new Gio.Settings({schema_id: POWER_SCHEMA});
        this._dimmingTarget = this._powerSettings.get_int('idle-brightness') / 100;
        this._dimmingEnabled = false;

        this._abTarget = -1.0;
        this._goalBaseTarget = -1.0;
        this._userBias = 0.0;

        this._nativeAlsReady = false;
        this._ambientEnabled = false;
        this._sensorProxy = null;
        this._lightClaimed = false;
        this._debounceTimerId = 0;
        this._debounceDirection = 0;
        this._pendingExtremeTarget = -1.0;
        this._animTimerId = 0;
        this._lastAnimUs = 0;

        Main.wm.addKeybinding(
            'screen-brightness-up',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessUp.bind(this));

        Main.wm.addKeybinding(
            'screen-brightness-up-monitor',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessUpCurrentMonitor.bind(this));

        Main.wm.addKeybinding(
            'screen-brightness-down',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessDown.bind(this));

        Main.wm.addKeybinding(
            'screen-brightness-down-monitor',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessDownCurrentMonitor.bind(this));

        Main.wm.addKeybinding(
            'screen-brightness-cycle',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessCycle.bind(this));

        Main.wm.addKeybinding(
            'screen-brightness-cycle-monitor',
            new Gio.Settings({schema_id: KEYBINDING_SCHEMA}),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            this._screenBrightnessCycleCurrentMonitor.bind(this));

        const monitorManager = global.backend.get_monitor_manager();
        monitorManager.connectObject('monitors-changed',
            this._monitorsChanged.bind(this), this);
        this._monitorsChanged();

        this._powerSettings.connectObject(
            'changed::idle-brightness', () => {
                this._dimmingTarget = this._powerSettings.get_int('idle-brightness') / 100;
                this._sync({showOSD: false});
            },
            'changed::ambient-enabled', () => {
                this._updateAmbientEnabled();
            },
            this);

        this._initSensorProxy();
    }

    _initSensorProxy() {
        if (Main.sessionMode?.isGreeter)
            return;

        Gio.DBusProxy.new(
            Gio.DBus.system,
            Gio.DBusProxyFlags.NONE,
            null,
            SENSOR_BUS_NAME,
            SENSOR_OBJECT_PATH,
            SENSOR_INTERFACE,
            null,
            (_source, res) => {
                try {
                    this._sensorProxy = Gio.DBusProxy.new_finish(res);
                } catch (_e) {
                    this._sensorProxy = null;
                    return;
                }

                this._sensorProxy.connectObject(
                    'g-properties-changed', (_proxy, changed) => {
                        const unpacked = changed.deepUnpack();
                        if ('HasAmbientLight' in unpacked)
                            this._updateAmbientEnabled();
                        if ('LightLevel' in unpacked && this._ambientEnabled)
                            this._onLuxSample(this._readProxyLux());
                    },
                    'notify::g-name-owner', () => {
                        this._lightClaimed = false;
                        this._updateAmbientEnabled();
                    },
                    this);

                this._updateAmbientEnabled();
            });
    }

    _readProxyLux() {
        if (!this._sensorProxy)
            return null;
        const vHas = this._sensorProxy.get_cached_property('HasAmbientLight');
        if (!vHas || !vHas.get_boolean())
            return null;
        const vLux = this._sensorProxy.get_cached_property('LightLevel');
        if (!vLux)
            return null;
        const lux = vLux.get_double();
        return Number.isFinite(lux) && lux >= 0.0 ? lux : null;
    }

    _claimSensorLight(claim) {
        if (!this._sensorProxy || !this._sensorProxy.g_name_owner)
            return;
        if (claim && !this._lightClaimed) {
            this._lightClaimed = true;
            this._sensorProxy.call(
                'ClaimLight', null, Gio.DBusCallFlags.NONE, 3000, null, null);
        } else if (!claim && this._lightClaimed) {
            this._lightClaimed = false;
            this._sensorProxy.call(
                'ReleaseLight', null, Gio.DBusCallFlags.NONE, 3000, null, null);
        }
    }

    _clearAlsTimers() {
        if (this._debounceTimerId) {
            GLib.Source.remove(this._debounceTimerId);
            this._debounceTimerId = 0;
        }
        this._debounceDirection = 0;
        if (this._animTimerId) {
            GLib.Source.remove(this._animTimerId);
            this._animTimerId = 0;
        }
        this._lastAnimUs = 0;
    }

    _updateAmbientEnabled() {
        if (Main.sessionMode?.isGreeter) {
            this._ambientEnabled = false;
            return;
        }

        const settingEnabled = this._powerSettings.get_boolean('ambient-enabled');
        const hasOwner = Boolean(this._sensorProxy?.g_name_owner);
        const vHas = this._sensorProxy?.get_cached_property('HasAmbientLight');
        const hasLight = Boolean(vHas && vHas.get_boolean());

        this._nativeAlsReady = hasOwner && hasLight;
        const shouldEnable = settingEnabled && this._nativeAlsReady;

        if (shouldEnable === this._ambientEnabled)
            return;

        this._ambientEnabled = shouldEnable;
        this._clearAlsTimers();

        if (shouldEnable) {
            this._userBias = 0.0;
            this._claimSensorLight(true);
            const lux = this._readProxyLux();
            if (lux !== null)
                this._commitGoalTarget(luxToRelativeBrightness(lux), {immediate: false});
        } else {
            this._claimSensorLight(false);
            this._abTarget = -1.0;
            this._goalBaseTarget = -1.0;
            this._userBias = 0.0;
            this._sync({showOSD: false});
        }
    }

    _onLuxSample(lux) {
        if (lux === null || !this._ambientEnabled)
            return;

        const rawTarget = luxToRelativeBrightness(lux);

        if (this._goalBaseTarget < 0.0 || this._abTarget < 0.0) {
            this._commitGoalTarget(rawTarget, {immediate: false});
            return;
        }

        const delta = rawTarget - this._goalBaseTarget;
        if (Math.abs(delta) < ALS_TARGET_DEADBAND) {
            if (this._debounceTimerId) {
                GLib.Source.remove(this._debounceTimerId);
                this._debounceTimerId = 0;
                this._debounceDirection = 0;
            }
            return;
        }

        const dir = delta > 0 ? 1 : -1;
        if (this._debounceTimerId && this._debounceDirection === dir) {
            if (dir > 0)
                this._pendingExtremeTarget = Math.min(this._pendingExtremeTarget, rawTarget);
            else
                this._pendingExtremeTarget = rawTarget;
            return;
        }

        if (this._debounceTimerId) {
            GLib.Source.remove(this._debounceTimerId);
            this._debounceTimerId = 0;
        }

        this._debounceDirection = dir;
        this._pendingExtremeTarget = rawTarget;
        const waitMs = dir > 0 ? BRIGHTEN_DEBOUNCE_MS : DARKEN_DEBOUNCE_MS;

        this._debounceTimerId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, waitMs, () => {
            this._debounceTimerId = 0;
            this._debounceDirection = 0;
            if (this._ambientEnabled)
                this._commitGoalTarget(this._pendingExtremeTarget, {immediate: false});
            return GLib.SOURCE_REMOVE;
        });
    }

    _commitGoalTarget(newGoal, {immediate = false} = {}) {
        this._goalBaseTarget = clamp(newGoal, 0.0, 1.0);

        if (immediate || this._abTarget < 0.0) {
            if (!immediate && this._globalScale) {
                this._abTarget = clamp(this._globalScale.value, 0.0, 1.0);
            } else {
                this._abTarget = this._goalBaseTarget;
                this._sync({showOSD: false});
                return;
            }
        }

        if (!this._animTimerId) {
            this._lastAnimUs = GLib.get_monotonic_time();
            this._animTimerId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT, ANIM_INTERVAL_MS, this._stepAlsAnimation.bind(this));
        }
    }

    _stepAlsAnimation() {
        if (!this._ambientEnabled || this._goalBaseTarget < 0.0) {
            this._animTimerId = 0;
            return GLib.SOURCE_REMOVE;
        }

        const nowUs = GLib.get_monotonic_time();
        const dtSec = clamp((nowUs - (this._lastAnimUs || nowUs)) / 1e6, 0.005, 0.25);
        this._lastAnimUs = nowUs;

        const diff = this._goalBaseTarget - this._abTarget;
        if (Math.abs(diff) <= 0.0015) {
            this._abTarget = this._goalBaseTarget;
            this._animTimerId = 0;
            this._sync({showOSD: false});
            return GLib.SOURCE_REMOVE;
        }

        const tau = diff > 0 ? 1.10 : 0.75;
        const alpha = dtSec / (tau + dtSec);
        const minStep = 0.04 * dtSec;
        const step = Math.max(Math.abs(diff) * alpha, Math.min(Math.abs(diff), minStep));

        this._abTarget += Math.sign(diff) * step;
        this._sync({showOSD: false});
        return GLib.SOURCE_CONTINUE;
    }

    get dimming() {
        return this._dimmingEnabled;
    }

    set dimming(enable) {
        this._dimmingEnabled = enable;
        this._sync({showOSD: false});
    }

    get autoBrightnessTarget() {
        return this._abTarget;
    }

    set autoBrightnessTarget(target) {
        // Bypass gsd-power's broken relative norm=1.5*lux outputs whenever our
        // native SensorProxy physical-lux engine is available.
        if (this._nativeAlsReady || this._sensorProxy)
            return;

        this._abTarget = target;
        this._sync({showOSD: false});
    }

    get globalScale() {
        return this._globalScale;
    }

    get scales() {
        return [...this._monitorScales.values()];
    }

    _screenBrightnessUp() {
        this._globalScale?.stepUp();
    }

    _screenBrightnessUpCurrentMonitor() {
        const monitor = global.backend.get_current_logical_monitor();
        this._monitorScales.get(monitor)?.stepUp();
    }

    _screenBrightnessDown() {
        this._globalScale?.stepDown();
    }

    _screenBrightnessDownCurrentMonitor() {
        const monitor = global.backend.get_current_logical_monitor();
        this._monitorScales.get(monitor)?.stepDown();
    }

    _screenBrightnessCycle() {
        this._globalScale?.cycleUp();
    }

    _screenBrightnessCycleCurrentMonitor() {
        const monitor = global.backend.get_current_logical_monitor();
        this._monitorScales.get(monitor)?.cycleUp();
    }

    _onUserBrightnessInput(desiredBrightness) {
        if (this._abTarget >= 0.0) {
            if (this._animTimerId) {
                GLib.Source.remove(this._animTimerId);
                this._animTimerId = 0;
            }
            if (this._goalBaseTarget >= 0.0)
                this._abTarget = this._goalBaseTarget;
            this._userBias = computeUserBias(this._abTarget, desiredBrightness);
        }
    }

    _monitorsChanged() {
        const monitors = global.backend
            .get_monitor_manager()
            .get_logical_monitors()
            .filter(lm => {
                return lm.get_monitors()
                    .some(m => m.get_backlight() && m.is_active());
            });

        this._monitorScales.clear();

        for (const monitor of monitors) {
            const scale = new MonitorBrightnessScale(monitor, 1.0);
            scale._scaleChanged = true;

            scale.connectObject(
                'backlights-changed', () => this._sync({showOSD: false}),
                'notify::value', () => {
                    if (this._inhibitUpdates)
                        return;
                    this._onUserBrightnessInput(scale.value);
                    scale._scaleChanged = true;
                    this._sync({fromUser: true});
                    this.emit('user-update');
                }, this);

            this._monitorScales.set(monitor, scale);
        }

        if (monitors.length === 0) {
            this._globalScale = null;
        } else if (!this._globalScale) {
            const maxSteps = Math.max(...[...this._monitorScales.values()]
                .map(s => s.nSteps));
            const nSteps = Math.min(maxSteps, SCALE_VALUE_N_STEPS);

            this._globalScale = new BrightnessScale(_('Brightness'), 1.0, nSteps);
            this._globalScale.connect('notify::value', () => {
                if (this._inhibitUpdates)
                    return;
                this._onUserBrightnessInput(this._globalScale.value);
                this._globalScaleChanged = true;
                this._sync({showOSD: false, fromUser: true});
                this.emit('user-update');
            });
        }

        this._sync({showOSD: false});
        this.emit('changed');
    }

    _sync({showOSD = true, fromUser = false} = {}) {
        if (!this._globalScale)
            return;
        if (this._inhibitUpdates)
            return;
        this._inhibitUpdates = true;

        for (const scale of this._monitorScales.values()) {
            if (scale.syncWithBacklight()) {
                this._dimmingEnabled = false;
            }
        }

        const changedScales = [...this._monitorScales.values()].filter(s => {
            const c = s._scaleChanged;
            s._scaleChanged = false;
            return c;
        });

        if (changedScales.length > 0) {
            const max = Math.max(...[...this._monitorScales.values()]
                .map(s => s.value));

            if (max > 0.01) {
                for (const scale of this._monitorScales.values())
                    scale.updateScaleFactor(max);
            }

            if (this._abTarget < 0.0 || fromUser)
                this._globalScale.value = max;

            if (showOSD)
                this._showOSD(changedScales);
        } else if (this._globalScaleChanged) {
            this._globalScaleChanged = false;

            for (const scale of this._monitorScales.values())
                scale.syncWithScale(this._globalScale);

            if (showOSD)
                this._showOSD(this._monitorScales.values());
        }

        let maxEffective = 0.0;
        for (const scale of this._monitorScales.values()) {
            const target = this._abTarget >= 0.0
                ? applyUserBias(this._abTarget, this._userBias)
                : scale.value;

            const max = this._dimmingEnabled ? this._dimmingTarget : 1.0;
            const brightness = Math.min(max, target);

            scale.setBacklight(brightness);
            if (!this._dimmingEnabled)
                scale.value = target;
            maxEffective = Math.max(maxEffective, target);
        }

        if (this._abTarget >= 0.0 && !fromUser && this._monitorScales.size > 0)
            this._globalScale.value = maxEffective;

        this._inhibitUpdates = false;
    }

    _showOSD(monitorScales) {
        const osdMonitors = {};
        for (const scale of monitorScales) {
            const level = scale.value;
            osdMonitors[scale.monitor.get_number()] = {level};
        }

        Main.osdWindowManager.show(
            Gio.Icon.new_for_string('display-brightness-symbolic'),
            null,
            osdMonitors
        );
    }
});

export const BrightnessScale = GObject.registerClass({
    Properties: {
        'value': GObject.ParamSpec.float(
            'value', null, null,
            GObject.ParamFlags.READWRITE,
            0, 1.0, 1.0),
    },
}, class BrightnessScale extends GObject.Object {
    constructor(name, value = 1.0, nSteps = SCALE_VALUE_N_STEPS) {
        super();

        this._name = name;
        this._value = value;
        this._nSteps = nSteps;
    }

    get name() {
        return this._name;
    }

    get value() {
        return this._value;
    }

    set value(value) {
        this._setValue(value);
    }

    get nSteps() {
        return this._nSteps;
    }

    stepUp() {
        this._setValue(Math.min(1.0, this._value + (1.0 / this._nSteps)));
    }

    stepDown() {
        this._setValue(Math.max(0.0, this._value - (1.0 / this._nSteps)));
    }

    cycleUp() {
        if (Math.abs(1.0 - this._value) < SCALE_VALUE_CHANGE_EPSILON)
            this._setValue(0.0);
        else
            this.stepUp();
    }

    _setValue(value) {
        this._value = clamp(value, 0.0, 1.0);
        this.notify('value');
    }
});

const MonitorBrightnessScale = GObject.registerClass({
    Signals: {
        'backlights-changed': {},
    },
}, class MonitorBrightnessScale extends BrightnessScale {
    constructor(monitor, value = 1.0) {
        const name = monitor.get_monitors()[0].get_display_name();

        const maxSteps = Math.max(...monitor.get_monitors()
            .filter(m => m.get_backlight() && m.is_active())
            .map(m => {
                const b = m.get_backlight();
                return b.brightnessMax - b.brightnessMin;
            }));
        const nSteps = Math.min(maxSteps, SCALE_VALUE_N_STEPS);

        super(name, value, nSteps);

        this._monitor = monitor;
        this._currentBacklightBrightness = -1;
        this._scaleFactor = 1.0;

        for (const backlight of this._getBacklights()) {
            backlight.connectObject('notify::brightness', () => {
                this.emit('backlights-changed');
            }, this);
        }
    }

    get monitor() {
        return this._monitor;
    }

    _getBacklights() {
        return this._monitor.get_monitors()
            .filter(m => m.get_backlight() && m.is_active())
            .map(m => m.get_backlight());
    }

    _getRelativeBrightness(backlight) {
        const {brightness, brightnessMin: min, brightnessMax: max} = backlight;
        return max > min ? (brightness - min) / (max - min) : 0.0;
    }

    _setRelativeBrightness(backlight, brightness) {
        const {brightnessMin: min, brightnessMax: max} = backlight;
        const hw = Math.round(min + ((max - min) * clamp(brightness, 0.0, 1.0)));
        if (backlight.brightness !== hw)
            backlight.brightness = hw;
        return hw;
    }

    syncWithBacklight() {
        const [backlight] = this._getBacklights();
        if (!backlight)
            return false;

        if (backlight.brightness === this._currentBacklightBrightness)
            return false;
        this._currentBacklightBrightness = backlight.brightness;

        this.value = this._getRelativeBrightness(backlight);
        return true;
    }

    setBacklight(brightness) {
        const backlights = this._getBacklights();
        if (backlights.length === 0)
            return;
        let hw = backlights[0].brightness;
        for (const backlight of backlights)
            hw = this._setRelativeBrightness(backlight, brightness);

        this._currentBacklightBrightness = hw;
    }

    updateScaleFactor(max) {
        this._scaleFactor = max > 0 ? this.value / max : 1.0;
    }
});
