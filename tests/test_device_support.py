#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline unit tests for TB378FC device-support scripts and packaging hygiene."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIBEXEC = ROOT / 'packaging/device-support/tree/usr/local/libexec'


def load_script(name, filename):
    path = LIBEXEC / filename
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestTorchAuthorization(unittest.TestCase):
    def setUp(self):
        self.torch = load_script('tb378fc_torch_service', 'tb378fc-torch-service.py')

    def test_root_always_authorized(self):
        self.assertTrue(self.torch.peer_authorized(0, 0))

    def test_active_seat_user_authorized_any_uid(self):
        with mock.patch.object(self.torch, 'active_seat_uid', return_value=1042):
            self.assertTrue(self.torch.peer_authorized(1042, 1042))

    def test_non_active_non_video_user_denied(self):
        fake_grp = mock.Mock(gr_gid=44, gr_mem=['alice'])
        fake_pw = mock.Mock(pw_gid=1099, pw_name='bob')
        with mock.patch.object(self.torch, 'active_seat_uid', return_value=1001), \
             mock.patch.object(self.torch.grp, 'getgrnam', return_value=fake_grp), \
             mock.patch.object(self.torch.pwd, 'getpwuid', return_value=fake_pw):
            self.assertFalse(self.torch.peer_authorized(1099, 1099))

    def test_video_group_member_authorized(self):
        fake_grp = mock.Mock(gr_gid=44, gr_mem=['alice'])
        fake_pw = mock.Mock(pw_gid=1005, pw_name='alice')
        with mock.patch.object(self.torch, 'active_seat_uid', return_value=None), \
             mock.patch.object(self.torch.grp, 'getgrnam', return_value=fake_grp), \
             mock.patch.object(self.torch.pwd, 'getpwuid', return_value=fake_pw):
            self.assertTrue(self.torch.peer_authorized(1005, 1005))


class TestVolumeSessionDiscovery(unittest.TestCase):
    def setUp(self):
        self.vol = load_script('tb378fc_volume_orientation', 'tb378fc-volume-orientation')

    def test_active_graphical_session_dynamic_uid(self):
        fake_pw = mock.Mock(pw_gid=1025)
        with mock.patch.object(
            self.vol.subprocess,
            'check_output',
            side_effect=[
                '3\n',
                'User=1025\nClass=user\nType=wayland\nActive=yes\n',
            ],
        ), mock.patch.object(self.vol.pwd, 'getpwuid', return_value=fake_pw), \
           mock.patch.object(self.vol.Path, 'is_socket', return_value=True):
            uid, gid, runtime_dir = self.vol.active_graphical_session()
            self.assertEqual((uid, gid, runtime_dir), (1025, 1025, '/run/user/1025'))


class TestGpuMonitorSysfs(unittest.TestCase):
    def setUp(self):
        self.gpu = load_script('tb378fc_gpu_monitor', 'tb378fc-gpu-monitor.py')

    def test_reads_standard_devfreq_without_legacy_module(self):
        with tempfile.TemporaryDirectory() as td:
            df = Path(td) / '3d00000.gpu'
            (df / 'device/power').mkdir(parents=True)
            (df / 'cur_freq').write_text('650000000\n')
            (df / 'min_freq').write_text('220000000\n')
            (df / 'max_freq').write_text('1100000000\n')
            (df / 'device/power/runtime_status').write_text('suspended\n')
            (df / 'device/power/runtime_active_time').write_text('1000\n')
            sampler = self.gpu.GpuSampler(devfreq_dir=df, legacy_sample=Path(td) / 'nonexistent')
            sample = sampler.read_sample()
            self.assertEqual(sample['frequency_hz'], 650000000)
            self.assertEqual(sample['usage_percent'], 0.0)
            self.assertEqual(sample['source'], 'sysfs_devfreq')


class TestInitramfsAndPackaging(unittest.TestCase):
    def test_initramfs_stages_tb378fc_shutdown(self):
        init_text = (ROOT / 'initramfs/init').read_text(encoding='utf-8')
        builder_text = (ROOT / 'tools/build-initramfs.sh').read_text(encoding='utf-8')
        self.assertIn('/bin/tb378fc-shutdown', builder_text)
        self.assertIn('/bin/tb378fc-shutdown', init_text)

    def test_electron_wayland_and_video_env(self):
        wl = (ROOT / 'packaging/device-support/tree/usr/lib/environment.d/60-tb378fc-wayland.conf').read_text(encoding='utf-8')
        self.assertIn('ELECTRON_OZONE_PLATFORM_HINT=auto', wl)
        self.assertIn('MOZ_ENABLE_WAYLAND=1', wl)
        self.assertIn('QT_QPA_PLATFORM="wayland;xcb"', wl)
        mesa_gen = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user-environment-generators/80-tb378fc-mesa').read_text(encoding='utf-8')
        self.assertIn('VK_DRIVER_FILES=', mesa_gen)
        vid = (ROOT / 'packaging/video-defaults/tree/usr/lib/environment.d/60-tb378fc-video.conf').read_text(encoding='utf-8')
        self.assertIn('GST_VA_ALL_DRIVERS=1', vid)
        self.assertIn('vah264dec:512', vid)

    def test_tb378fc_vulkan_prefers_private_mesa_prefix(self):
        vk_text = (ROOT / 'packaging/device-support/tree/usr/local/bin/tb378fc-vulkan').read_text(encoding='utf-8')
        self.assertIn('/usr/lib/tb378fc/mesa/share/vulkan/icd.d/freedreno_icd.aarch64.json', vk_text)
        self.assertIn('/usr/lib/tb378fc/turnip/share/vulkan/icd.d/freedreno_icd.aarch64.json', vk_text)

    def test_initramfs_gates_debug_services(self):
        init_text = (ROOT / 'initramfs/init').read_text(encoding='utf-8')
        self.assertIn('tb378fc.debug=1', init_text)
        self.assertIn('[ "$debug" = "1" ]', init_text)

    def test_symlinks_all_relative_and_exist(self):
        sym = json.loads((ROOT / 'packaging/device-support/symlinks.json').read_text(encoding='utf-8'))
        tree = ROOT / 'packaging/device-support/tree'
        for link, target in sym.items():
            self.assertFalse(target.startswith('/'), f'absolute symlink: {link} -> {target}')
            self.assertTrue((tree / link).parent.joinpath(target).resolve().exists())


    def test_camera_vcm_udev_and_kconfig(self):
        kcfg = (ROOT / 'kernel/configs/tb378fc.config').read_text(encoding='utf-8')
        self.assertIn('CONFIG_VIDEO_DW9807_VCM=m', kcfg)
        dma = (ROOT / 'packaging/device-support/tree/etc/udev/rules.d/82-tb378fc-dmaheap75.rules').read_text(encoding='utf-8')
        self.assertIn('TAG+="uaccess"', dma)
        v4l = (ROOT / 'packaging/device-support/tree/etc/udev/rules.d/60-persistent-v4l.rules').read_text(encoding='utf-8')
        self.assertIn('KERNEL!="v4l-subdev*"', v4l)
        fw = (ROOT / 'tools/install-tb378fc-firmware.py').read_text(encoding='utf-8')
        self.assertIn('brhbtfw', fw)
        self.assertIn('novatek_ts_fw.bin', fw)


    def test_default_boot_docs_use_gki_v4_not_fastboot_boot_v2(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        flashing = (ROOT / 'docs/FLASHING.zh-CN.md').read_text(encoding='utf-8')
        triplet = (ROOT / 'tools/build-boot-triplet.py').read_text(encoding='utf-8')
        self.assertIn('build-boot-triplet.py', readme)
        self.assertIn('build-boot-triplet.py', flashing)
        self.assertNotIn('fastboot boot boot-v2.img', readme)
        self.assertNotIn('fastboot boot boot-v2.img', flashing)
        self.assertIn('--with-vbmeta', triplet)
        self.assertIn('--header_version", "4"', triplet)


    def test_pipewire_camera_dropin_does_not_mask_system_libcamera_or_egl_isp(self):
        for svc in ('pipewire', 'wireplumber'):
            p = ROOT / f'packaging/device-support/tree/etc/systemd/user/{svc}.service.d/95-tb378fc-camera.conf'
            txt = p.read_text(encoding='utf-8')
            self.assertNotIn('LIBCAMERA_SOFTISP_MODE=cpu', txt)
            self.assertNotIn('/usr/lib/tb378fc/camera', txt)


if __name__ == '__main__':
    unittest.main()
