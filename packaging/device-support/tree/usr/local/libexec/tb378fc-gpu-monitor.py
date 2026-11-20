#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
"""Adreno GPU monitor reading standard kernel devfreq and runtime PM sysfs."""
from collections import deque
import json
from pathlib import Path
import time

DEVFREQ_DIR = Path('/sys/class/devfreq/3d00000.gpu')
LEGACY_SAMPLE = Path('/sys/kernel/tb378fc_gpu/sample')


class GpuSampler:
    def __init__(self, devfreq_dir=DEVFREQ_DIR, legacy_sample=LEGACY_SAMPLE):
        self.devfreq_dir = Path(devfreq_dir)
        self.legacy_sample = Path(legacy_sample)
        self._prev_ts = None
        self._prev_active_ms = None

    def read_sample(self):
        frequency = int((self.devfreq_dir / 'cur_freq').read_text().strip())
        min_freq = int((self.devfreq_dir / 'min_freq').read_text().strip()) if (self.devfreq_dir / 'min_freq').is_file() else 0
        max_freq = int((self.devfreq_dir / 'max_freq').read_text().strip()) if (self.devfreq_dir / 'max_freq').is_file() else 0

        if self.legacy_sample.is_file():
            raw = {
                k: int(v)
                for k, v in (line.split('=', 1) for line in self.legacy_sample.read_text().splitlines() if '=' in line)
            }
            if raw.get('suspended') or raw.get('polling_stopped'):
                return {'frequency_hz': frequency, 'min_frequency_hz': min_freq, 'max_frequency_hz': max_freq,
                        'usage_percent': 0.0, 'state': 'Idle / power saving', 'source': 'legacy_telemetry'}
            if raw.get('total', 0) > 0 and raw.get('busy', 0) <= raw['total']:
                return {'frequency_hz': frequency, 'min_frequency_hz': min_freq, 'max_frequency_hz': max_freq,
                        'usage_percent': 100.0 * raw['busy'] / raw['total'], 'state': 'Active', 'source': 'legacy_telemetry'}

        power_dir = self.devfreq_dir / 'device/power'
        pm_status = (power_dir / 'runtime_status').read_text().strip() if (power_dir / 'runtime_status').is_file() else 'unknown'
        now = time.monotonic()
        active_path = power_dir / 'runtime_active_time'
        usage = None
        if active_path.is_file():
            active_ms = int(active_path.read_text().strip())
            if self._prev_ts is not None and now > self._prev_ts:
                delta_wall_ms = (now - self._prev_ts) * 1000.0
                delta_active_ms = max(0, active_ms - self._prev_active_ms)
                usage = min(100.0, 100.0 * delta_active_ms / delta_wall_ms)
            self._prev_ts = now
            self._prev_active_ms = active_ms

        if pm_status == 'suspended':
            usage = 0.0
            state = 'Idle / runtime suspended'
        elif usage is not None:
            state = 'Active (runtime PM duty cycle)'
        else:
            state = f'Devfreq ({pm_status})'

        return {
            'frequency_hz': frequency,
            'min_frequency_hz': min_freq,
            'max_frequency_hz': max_freq,
            'usage_percent': usage,
            'state': state,
            'source': 'sysfs_devfreq',
        }


def detect_video_codec():
    v4l_dir = Path('/sys/class/video4linux')
    if not v4l_dir.is_dir():
        return 'Video hardware codec (Iris): unavailable'
    iris = []
    for node in sorted(v4l_dir.glob('video*')):
        try:
            name = (node / 'name').read_text().strip()
        except OSError:
            continue
        if 'iris' in name.lower() or 'qcom-iris' in name.lower():
            iris.append(f'{node.name} ({name})')
    if iris:
        return 'Video hardware codec (Iris): ' + ', '.join(iris)
    return 'Video hardware codec (Iris): not bound'


def run_gui():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib

    class Monitor(Gtk.Application):
        def __init__(self):
            super().__init__(application_id='org.lenovo.TB378FC.GpuMonitor')
            self.connect('activate', self.activate)
            self.sampler = GpuSampler()
            self.history = deque(maxlen=90)
            self.window = None

        def activate(self, app):
            if self.window:
                self.window.present()
                return
            self.window = Gtk.ApplicationWindow(application=app, title='Adreno GPU Monitor')
            self.window.set_default_size(540, 360)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            for side in ('top', 'bottom', 'start', 'end'):
                getattr(box, 'set_margin_' + side)(20)
            self.window.set_child(box)
            title = Gtk.Label(label='Adreno 830v1 (SM8750P)', xalign=0)
            title.add_css_class('title-1')
            box.append(title)
            api = Gtk.Label(label='OpenGL 4.6  ·  OpenGL ES 3.2  ·  Vulkan 1.4', xalign=0)
            api.add_css_class('dim-label')
            box.append(api)
            self.freq = Gtk.Label(xalign=0)
            self.freq.add_css_class('title-2')
            box.append(self.freq)
            self.load = Gtk.Label(xalign=0)
            box.append(self.load)
            self.bar = Gtk.ProgressBar()
            box.append(self.bar)
            self.history_label = Gtk.Label(label='', xalign=0)
            self.history_label.set_selectable(False)
            box.append(self.history_label)
            self.state = Gtk.Label(xalign=0)
            self.state.add_css_class('dim-label')
            box.append(self.state)
            note = Gtk.Label(
                label='Reads standard kernel devfreq and runtime PM sysfs once per second.',
                xalign=0,
            )
            note.set_wrap(True)
            note.add_css_class('dim-label')
            box.append(note)
            video = Gtk.Label(label=detect_video_codec(), xalign=0)
            video.set_wrap(True)
            box.append(video)
            self.refresh()
            GLib.timeout_add_seconds(1, self.refresh)
            self.window.present()

        def refresh(self):
            try:
                sample = self.sampler.read_sample()
                usage = sample['usage_percent']
                self.freq.set_text(f"Clock   {sample['frequency_hz'] / 1e6:.0f} MHz")
                self.load.set_text(
                    'GPU activity   sampling...' if usage is None else f'GPU activity   {usage:.1f}%'
                )
                self.bar.set_fraction(0.0 if usage is None else usage / 100.0)
                self.state.set_text(sample['state'])
                self.history.append(usage)
            except (OSError, KeyError, ValueError) as error:
                self.load.set_text('GPU telemetry unavailable')
                self.state.set_text(str(error))
                self.history.append(None)
            glyphs = '\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588'
            self.history_label.set_text(
                ''.join(' ' if v is None else glyphs[min(7, int(v * 7 / 100))] for v in list(self.history)[-45:])
            )
            return True

    import sys
    Monitor().run(sys.argv)


if __name__ == '__main__':
    import sys
    if '--sample' in sys.argv:
        sampler = GpuSampler()
        sampler.read_sample()
        time.sleep(0.2)
        print(json.dumps(sampler.read_sample(), indent=2))
    else:
        run_gui()
