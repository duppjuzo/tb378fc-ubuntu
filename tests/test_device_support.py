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
        dma = (ROOT / 'packaging/device-support/tree/etc/udev/rules.d/70-tb378fc-media-dmaheap.rules').read_text(encoding='utf-8')
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




class TestThermalGuardAndPowerProfiles(unittest.TestCase):
    def setUp(self):
        self.tg = load_script('tb378fc_thermal_guard', 'tb378fc-thermal-guard.py')

    def test_profile_state_persistence_and_holds(self):
        with tempfile.TemporaryDirectory() as td:
            sf = Path(td) / 'power-profile'
            st = self.tg.ProfileState(state_file=sf)
            self.assertEqual(st.active_profile, 'balanced')
            st.set_active_profile('performance')
            self.assertEqual(st.active_profile, 'performance')
            self.assertEqual(sf.read_text(encoding='utf-8').strip(), 'performance')

            st2 = self.tg.ProfileState(state_file=sf)
            self.assertEqual(st2.active_profile, 'performance')
            st2.set_active_profile('balanced')
            c1 = st2.hold_profile('power-saver', 'battery', 'app1', ':1.10')
            self.assertEqual(st2.active_profile, 'power-saver')
            c2 = st2.hold_profile('performance', 'game', 'app2', ':1.20')
            self.assertEqual(st2.active_profile, 'performance')
            self.assertEqual(st2.release_sender(':1.20'), [c2])
            self.assertEqual(st2.active_profile, 'power-saver')
            self.assertTrue(st2.release_profile(c1))
            self.assertEqual(st2.active_profile, 'balanced')

    def test_profile_thermal_walls_and_uncapped_performance(self):
        perf = self.tg.PROFILE_CONFIG['performance']
        bal = self.tg.PROFILE_CONFIG['balanced']
        ps = self.tg.PROFILE_CONFIG['power-saver']
        self.assertEqual(perf['passive_trip_mc'], 118_000)
        self.assertEqual(bal['passive_trip_mc'], 85_000)
        self.assertEqual(ps['passive_trip_mc'], 75_000)
        self.assertGreater(perf['caps'][0], bal['caps'][0])
        self.assertGreater(bal['caps'][0], ps['caps'][0])
        self.assertEqual(len(perf['thermal_stages']), 1)

    def test_6w_closed_loop_power_cap_controller(self):
        ctrl = self.tg.PowerCapController()
        self.assertEqual(ctrl.update('Charging', None), self.tg.POWER_CAP_AC_FALLBACK_LEVEL)
        ctrl.reset()
        # Sustained >6W discharging steps down smoothly
        self.assertEqual(ctrl.update('Discharging', 6.8), 1)
        self.assertEqual(ctrl.update('Discharging', 7.2), 2)
        self.assertEqual(ctrl.update('Discharging', 8.5), 4)
        # First low tick brings EMA from 7.75W -> 5.375W (still >5.2W); then 3 consecutive <5.2W ticks recover 1 step
        self.assertEqual(ctrl.update('Discharging', 3.0), 4)
        self.assertEqual(ctrl.update('Discharging', 3.0), 4)
        self.assertEqual(ctrl.update('Discharging', 3.0), 4)
        self.assertEqual(ctrl.update('Discharging', 3.0), 3)

    def test_read_battery_discharge_watts_and_uclamp_helper(self):
        with tempfile.TemporaryDirectory() as td:
            psy = Path(td) / 'bat'
            psy.mkdir()
            (psy / 'status').write_text('Discharging\n')
            (psy / 'voltage_now').write_text('4000000\n')
            (psy / 'current_now').write_text('-1500000\n')
            st, w = self.tg.read_battery_discharge_watts(psy)
            self.assertEqual(st, 'Discharging')
            self.assertAlmostEqual(w, 6.0, places=3)

            (psy / 'status').write_text('Charging\n')
            st2, w2 = self.tg.read_battery_discharge_watts(psy)
            self.assertEqual(st2, 'Charging')
            self.assertIsNone(w2)

            cg = Path(td) / 'cg/user.slice/user-1042.slice/user@1042.service'
            for s in ('session.slice', 'app.slice', 'background.slice'):
                (cg / s).mkdir(parents=True)
            n = self.tg.apply_user_slice_uclamp(self.tg.PROFILE_CONFIG['balanced']['uclamp'], Path(td) / 'cg')
            self.assertEqual(n, 3)
            self.assertEqual((cg / 'session.slice/cpu.uclamp.min').read_text().strip(), '12.00')
            self.assertEqual((cg / 'background.slice/cpu.uclamp.max').read_text().strip(), '30.00')


class TestV4l2CameraBridgeAndAv1(unittest.TestCase):
    def test_bridge_script_constants_and_blank_frame(self):
        br = load_script('tb378fc_v4l2_camera_bridge', 'tb378fc-v4l2-camera-bridge.py')
        self.assertEqual(br.V4L2_EVENT_PRI_CLIENT_USAGE, 0x10E00001)
        frame = br.make_blank_yuyv(16, 8)
        self.assertEqual(len(frame), 16 * 8 * 2)

    def test_v4l2loopback_modprobe_wireplumber_and_av1_unlocked(self):
        mp = (ROOT / 'packaging/device-support/tree/etc/modprobe.d/tb378fc-v4l2loopback.conf').read_text(encoding='utf-8')
        self.assertIn('exclusive_caps=1,1', mp)
        self.assertIn('video_nr=30,31', mp)
        wp = (ROOT / 'packaging/device-support/tree/usr/share/wireplumber/wireplumber.conf.d/52-tb378fc-hide-v4l2loopback.conf').read_text(encoding='utf-8')
        self.assertIn('v4l2 loopback', wp)
        bg = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user/background.slice.d/50-tb378fc-sched.conf').read_text(encoding='utf-8')
        self.assertIn('AllowedCPUs=0-5', bg)
        ff = (ROOT / 'packaging/video-defaults/tree/usr/lib/firefox/browser/defaults/preferences/tb378fc-video.js').read_text(encoding='utf-8')
        self.assertIn('pref("media.av1.enabled", true);', ff)
        self.assertIn('pref("gfx.webrender.compositor", false);', ff)
        self.assertIn('pref("gfx.webrender.compositor.force-enabled", false);', ff)
        lock = json.loads((ROOT / 'packaging/iris-vaapi/source.json').read_text(encoding='utf-8'))
        self.assertIn('0006-promote-showable-av1-frames.patch', lock['patches'])
        self.assertNotIn('0005-retain-av1-capability-boundary.patch', lock['patches'])



class TestUfsMemoryOomdAndSecurityCenter(unittest.TestCase):
    def test_camera_bridge_skips_system_users_like_gdm(self):
        svc = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user/tb378fc-v4l2-camera-bridge.service').read_text(encoding='utf-8')
        self.assertIn('ConditionUser=!@system', svc)
        self.assertIn('ConditionUser=!gdm', svc)
        self.assertIn('ConditionUser=!gdm-greeter', svc)
        py = (ROOT / 'packaging/device-support/tree/usr/local/libexec/tb378fc-v4l2-camera-bridge.py').read_text(encoding='utf-8')
        self.assertIn('uid < 1000 or uid >= 60000', py)

    def test_ufs_iosched_and_journald_write_amplification(self):
        rule = (ROOT / 'packaging/device-support/tree/etc/udev/rules.d/60-tb378fc-ufs-iosched.rules').read_text(encoding='utf-8')
        self.assertIn('KERNELS=="1d84000.ufs"', rule)
        self.assertIn('ATTR{queue/scheduler}="kyber"', rule)
        self.assertIn('ATTR{queue/read_ahead_kb}="256"', rule)
        jd = (ROOT / 'packaging/device-support/tree/etc/systemd/journald.conf.d/90-tb378fc-journald.conf').read_text(encoding='utf-8')
        self.assertIn('SystemMaxUse=128M', jd)
        self.assertIn('MaxFileSec=7day', jd)

    def test_zram_ordering_vm_sysctl_and_oomd_slices(self):
        zsvc = (ROOT / 'packaging/device-support/tree/etc/systemd/system/tb378fc-zram.service').read_text(encoding='utf-8')
        self.assertIn('DefaultDependencies=no', zsvc)
        self.assertIn('After=local-fs-pre.target', zsvc)
        self.assertNotIn('After=local-fs.target', [l.strip() for l in zsvc.splitlines() if not l.strip().startswith('#')])
        self.assertIn('Before=swap.target systemd-oomd.service', zsvc)
        sym = json.loads((ROOT / 'packaging/device-support/symlinks.json').read_text(encoding='utf-8'))
        self.assertIn('etc/systemd/system/swap.target.wants/tb378fc-zram.service', sym)
        vm = (ROOT / 'packaging/device-support/tree/etc/sysctl.d/90-tb378fc-vm.conf').read_text(encoding='utf-8')
        self.assertIn('vm.watermark_scale_factor = 125', vm)
        self.assertIn('vm.vfs_cache_pressure = 60', vm)
        self.assertIn('vm.swappiness = 130', vm)
        oomd = (ROOT / 'packaging/device-support/tree/etc/systemd/oomd.conf.d/50-tb378fc-oomd.conf').read_text(encoding='utf-8')
        self.assertIn('SwapUsedLimit=90%', oomd)
        app_s = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user/app.slice.d/50-tb378fc-oomd.conf').read_text(encoding='utf-8')
        self.assertIn('ManagedOOMSwap=kill', app_s)
        self.assertIn('ManagedOOMMemoryPressure=kill', app_s)
        bg_s = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user/background.slice.d/50-tb378fc-sched.conf').read_text(encoding='utf-8')
        self.assertIn('ManagedOOMMemoryPressureLimit=40%', bg_s)
        sess_s = (ROOT / 'packaging/device-support/tree/usr/lib/systemd/user/session.slice.d/50-tb378fc-sched.conf').read_text(encoding='utf-8')
        self.assertIn('ManagedOOMPreference=omit', sess_s)

    def test_security_center_and_snap_store_mesa2404_wrappers(self):
        for app in ('desktop-security-center', 'snap-store'):
            wrapper = (ROOT / f'packaging/device-support/tree/usr/local/bin/tb378fc-{app}').read_text(encoding='utf-8')
            self.assertIn('LIBGL_ALWAYS_SOFTWARE=1', wrapper)
            self.assertIn('GALLIUM_DRIVER=llvmpipe', wrapper)
            self.assertIn(f'exec /snap/bin/{app}', wrapper)
            desk = (ROOT / f'packaging/device-support/tree/usr/local/share/applications/{app}_{app}.desktop').read_text(encoding='utf-8')
            self.assertIn(f'Exec=/usr/local/bin/tb378fc-{app} %U', desk)


    def test_mglru_and_ufs_hwmon_kconfig_and_v4l2loopback_packaging(self):
        kcfg = (ROOT / 'kernel/configs/tb378fc.config').read_text(encoding='utf-8')
        self.assertIn('CONFIG_LRU_GEN=y', kcfg)
        self.assertIn('CONFIG_LRU_GEN_ENABLED=y', kcfg)
        self.assertIn('# CONFIG_LRU_GEN_STATS is not set', kcfg)
        self.assertIn('CONFIG_SCSI_UFS_HWMON=y', kcfg)
        bk = (ROOT / 'tools/build-kernel.sh').read_text(encoding='utf-8')
        self.assertIn('v4l2loopback.ko', bk)
        bd = (ROOT / 'tools/build-debs.py').read_text(encoding='utf-8')
        self.assertIn('extra_modules', bd)


    def test_no_qbootctl_slot_switch_bug(self):
        self.assertFalse((ROOT / 'packaging/device-support/tree/usr/local/sbin/tb378fc-slot-switch').exists())
        desk = (ROOT / 'packaging/device-support/tree/usr/share/applications/tb378fc-switch-to-android.desktop').read_text(encoding='utf-8')
        self.assertNotIn('qbootctl', desk)
        self.assertNotIn('tb378fc-slot-switch', desk)


    def test_same_slot_switch_supports_configured_slot_b(self):
        import subprocess, tempfile, zipfile
        sw = (ROOT / 'switch/tb378fc-switch').read_text(encoding='utf-8')
        self.assertIn('TB378FC_SLOT=', sw)
        self.assertIn('expected active slot $expected_slot, got $SLOT', sw)
        with tempfile.TemporaryDirectory() as td:
            out_zip = Path(td) / 'switch-b.zip'
            subprocess.run(
                ['python3', str(ROOT / 'tools/package-switch.py'),
                 '--serial', 'TEST1234', '--soc-serial', '987654321',
                 '--slot', '_b', '--output', str(out_zip)],
                check=True,
                capture_output=True,
                text=True,
            )
            with zipfile.ZipFile(out_zip) as zf:
                conf = zf.read('device.conf').decode('utf-8')
                self.assertIn('TB378FC_SLOT=_b', conf)
                self.assertIn('tb378fc-switch', zf.namelist())
                self.assertNotIn('qbootctl', ' '.join(zf.namelist()))
            side_conf = out_zip.with_suffix('.device.conf').read_text(encoding='utf-8')
            self.assertIn('TB378FC_SLOT=_b', side_conf)


    def test_backlight_persistence_and_als_hysteresis(self):
        gs = (ROOT / 'packaging/device-support/tree/usr/share/glib-2.0/schemas/90_tb378fc-power.gschema.override').read_text(encoding='utf-8')
        self.assertIn('[org.gnome.settings-daemon.plugins.power]', gs)
        self.assertIn('ambient-enabled = false', gs)
        gdm_cfg = (ROOT / 'packaging/device-support/tree/usr/share/gdm/dconf/90-tb378fc-greeter').read_text(encoding='utf-8')
        self.assertIn('ambient-enabled=false', gdm_cfg)
        self.assertIn('idle-dim=false', gdm_cfg)
        gdm_lck = (ROOT / 'packaging/device-support/tree/usr/share/gdm/dconf/locks/90-tb378fc-greeter-locks').read_text(encoding='utf-8')
        self.assertIn('/org/gnome/settings-daemon/plugins/power/ambient-enabled', gdm_lck)
        bdebs = (ROOT / 'tools/build-debs.py').read_text(encoding='utf-8')
        self.assertIn('glib-compile-schemas', bdebs)
        self.assertIn('/usr/share/gdm/generate-config', bdebs)
        sens = (ROOT / 'packaging/sensors/tb378fc-sensord.c').read_text(encoding='utf-8')
        self.assertIn('fmax(2.0, stream->self->light * 0.08)', sens)


    def test_bluetooth_aac_wireplumber_bqr_and_kconfig(self):
        wp_bt = (ROOT / 'packaging/audio/tree/usr/share/wireplumber/wireplumber.conf.d/51-tb378fc-bluetooth.conf').read_text(encoding='utf-8')
        self.assertIn('bluez5.enable-sbc-xq = true', wp_bt)
        self.assertIn('bluez5.enable-msbc = true', wp_bt)
        self.assertIn('bluez5.enable-hw-volume = true', wp_bt)
        self.assertIn('bluez5.a2dp.aac.bitratemode = 0', wp_bt)
        self.assertIn('bluez5.a2dp.ldac.quality = "auto"', wp_bt)

        bt_svc = (ROOT / 'packaging/device-support/tree/etc/systemd/system/bluetooth.service.d/92-tb378fc-native.conf').read_text(encoding='utf-8')
        self.assertIn('--kernel=330859bc-7506-492d-9370-9a6f0614037f', bt_svc)
        # Never turn on Offload Codecs or LL Privacy unconditionally on WCN7860 UART
        for line in bt_svc.splitlines():
            if not line.lstrip().startswith('#'):
                self.assertNotIn('a6695ace-ee7f-4fb9-881a-5fac66c629af', line)
                self.assertNotIn('15c0a148-c273-11ea-b3de-0242ac130004', line)

        bdebs = (ROOT / 'tools/build-debs.py').read_text(encoding='utf-8')
        self.assertIn('libspa-0.2-modules-extra', bdebs)
        self.assertIn('libfdk-aac2', bdebs)

        brootfs = (ROOT / 'tools/build-desktop-rootfs.py').read_text(encoding='utf-8')
        self.assertIn('"libspa-0.2-modules-extra"', brootfs)
        self.assertIn('"libfdk-aac2"', brootfs)
        self.assertIn('libspa-codec-bluez5-aac.so', brootfs)

        kcfg = (ROOT / 'kernel/configs/tb378fc.config').read_text(encoding='utf-8')
        self.assertIn('CONFIG_BT_AOSPEXT=y', kcfg)
        self.assertIn('CONFIG_BT_HIDP=m', kcfg)

    def test_prime_boost_4320mhz_and_power_source_wifi_linkage(self):
        tg = load_script('tb378fc_thermal_guard_boost', 'tb378fc-thermal-guard.py')
        self.assertEqual(tg.DEFAULT_MAX_P6, 4_320_000)
        with tempfile.TemporaryDirectory() as td:
            p6 = Path(td) / 'policy6'
            p6.mkdir()
            (p6 / 'scaling_available_frequencies').write_text('1017600 2438400 3283200 4089600\n')
            (p6 / 'scaling_boost_frequencies').write_text('4204800 4320000\n')
            opps = tg.read_policy_opps(p6)
            self.assertEqual(opps[-2:], [4204800, 4320000])
            self.assertEqual(tg.snap_opp(4320000, opps), 4320000)

            usb = Path(td) / 'usb'
            bat = Path(td) / 'bat'
            usb.mkdir()
            bat.mkdir()
            (usb / 'online').write_text('1\n')
            self.assertTrue(tg.read_ac_online(usb, bat))
            (usb / 'online').write_text('0\n')
            self.assertFalse(tg.read_ac_online(usb, bat))

            sf = Path(td) / 'power-profile'
            st = tg.ProfileState(state_file=sf)
            self.assertEqual(st.active_profile, 'balanced')
            self.assertTrue(st.apply_power_source(True))
            self.assertEqual(st.active_profile, 'performance')
            self.assertTrue(st.apply_power_source(False))
            self.assertEqual(st.active_profile, 'balanced')
            # Manual power-saver must NOT be overwritten when unplugging AC
            st.set_active_profile('power-saver')
            self.assertFalse(st.apply_power_source(False))
            self.assertEqual(st.active_profile, 'power-saver')
            # Plugging AC back in promotes to performance
            self.assertTrue(st.apply_power_source(True))
            self.assertEqual(st.active_profile, 'performance')

            net = Path(td) / 'net'
            (net / 'wlp1s0/wireless').mkdir(parents=True)
            (net / 'lo').mkdir(parents=True)
            with mock.patch.object(tg.shutil, 'which', return_value='/usr/sbin/iw'), \
                 mock.patch.object(tg.subprocess, 'run', return_value=mock.Mock(returncode=0)) as mrun:
                self.assertEqual(tg.sync_wifi_power_save(True, net), 1)
                mrun.assert_called_once_with(
                    ['/usr/sbin/iw', 'dev', 'wlp1s0', 'set', 'power_save', 'on'],
                    stdout=tg.subprocess.DEVNULL,
                    stderr=tg.subprocess.DEVNULL,
                    check=False,
                )

    def test_system_optimizations_boot_irq_mglru_bbr_and_video_encode(self):
        tg_svc = (ROOT / 'packaging/device-support/tree/etc/systemd/system/tb378fc-thermal-guard.service').read_text(encoding='utf-8')
        self.assertNotIn('Conflicts=power-profiles-daemon.service', tg_svc)
        self.assertIn('Alias=power-profiles-daemon.service', tg_svc)

        vm = (ROOT / 'packaging/device-support/tree/etc/sysctl.d/90-tb378fc-vm.conf').read_text(encoding='utf-8')
        for expected in (
            'vm.watermark_boost_factor = 0',
            'fs.inotify.max_user_watches = 524288',
            'fs.inotify.max_user_instances = 1024',
            'net.core.default_qdisc = fq',
            'net.ipv4.tcp_congestion_control = bbr',
            'net.ipv4.tcp_fastopen = 3',
        ):
            self.assertIn(expected, vm)

        mglru = (ROOT / 'packaging/device-support/tree/etc/tmpfiles.d/90-tb378fc-mglru.conf').read_text(encoding='utf-8')
        self.assertIn('/sys/kernel/mm/lru_gen/min_ttl_ms', mglru)
        self.assertIn('1000', mglru)

        hw_init = (ROOT / 'packaging/device-support/tree/usr/local/sbin/tb378fc-hw-init').read_text(encoding='utf-8')
        for token in ('gpu-irq', '406000\\.mailbox', 'apps_rsc', 'smp_affinity_list'):
            self.assertIn(token, hw_init)

        nm = (ROOT / 'packaging/device-support/tree/usr/lib/NetworkManager/conf.d/90-tb378fc-wifi-mac.conf').read_text(encoding='utf-8')
        self.assertIn('[connectivity]', nm)
        self.assertIn('enabled=false', nm)

        chrony = (ROOT / 'packaging/device-support/tree/etc/chrony/sources.d/90-tb378fc-cn-ntp.sources').read_text(encoding='utf-8')
        self.assertIn('203.107.6.88', chrony)

        # Guard Wi-Fi 7 6GHz@320MHz + 5GHz@160MHz: never force ieee80211_regdom=CN
        for p in (ROOT / 'packaging').rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                self.assertNotIn('ieee80211_regdom=CN', p.read_text(encoding='utf-8', errors='ignore'))

        vid = (ROOT / 'packaging/video-defaults/tree/usr/lib/environment.d/60-tb378fc-video.conf').read_text(encoding='utf-8')
        self.assertIn('vah264enc:512', vid)
        self.assertIn('vah265enc:512', vid)
        chr_flags = (ROOT / 'packaging/video-defaults/tree/etc/chromium.d/tb378fc-wayland-vaapi').read_text(encoding='utf-8')
        self.assertIn('AcceleratedVideoEncoder', chr_flags)
        self.assertIn('AcceleratedVideoDecodeLinuxZeroCopyGL', chr_flags)
        self.assertIn('--enable-wayland-ime', chr_flags)
        self.assertIn('--wayland-text-input-version=3', chr_flags)
        sync_chrome = (ROOT / 'packaging/video-defaults/tree/usr/local/libexec/tb378fc-sync-chrome-vaapi').read_text(encoding='utf-8')
        self.assertIn('--enable-wayland-ime --wayland-text-input-version=3', sync_chrome)
        osk_js = (ROOT / 'packaging/device-support/tree/usr/local/share/tb378fc/gnome-shell/ui/keyboard.js').read_text(encoding='utf-8')
        self.assertNotIn('this._seat.get_touch_mode() && this._lastDeviceIsTouchscreen()', osk_js)
        self.assertIn('_maybeOpenForWaylandFocus', osk_js)
        self.assertIn('global.stage.connectObject(\n            \x27captured-event\x27', osk_js)

        iris_lock = json.loads((ROOT / 'packaging/iris-vaapi/source.json').read_text(encoding='utf-8'))
        self.assertIn('0007-chromium-vaapi-encode-compat.patch', iris_lock['patches'])

        kcfg = (ROOT / 'kernel/configs/tb378fc.config').read_text(encoding='utf-8')
        for ksym in (
            'CONFIG_TCP_CONG_BBR=y',
            'CONFIG_NET_SCH_FQ=y',
            'CONFIG_NET_SCH_CAKE=m',
            'CONFIG_ZRAM_MULTI_COMP=y',
            'CONFIG_ZRAM_WRITEBACK=y',
            'CONFIG_DEBUG_INFO_BTF=y',
            'CONFIG_ATH12K=m',
            'CONFIG_BT_HCIUART=m',
            'CONFIG_QCOM_Q6V5_PAS=m',
        ):
            self.assertIn(ksym, kcfg)

        brootfs = (ROOT / 'tools/build-desktop-rootfs.py').read_text(encoding='utf-8')
        self.assertIn('systemd-networkd-wait-online.service', brootfs)
        self.assertIn('prompting-client', brootfs)
        self.assertIn('colord.service', brootfs)


    def test_lenovo_pogo_keyboard_patch_and_protocol(self):
        kcfg = (ROOT / 'kernel/configs/tb378fc.config').read_text(encoding='utf-8')
        self.assertIn('# CONFIG_KEYBOARD_ONEPLUS_POGO is not set', kcfg)
        self.assertIn('CONFIG_KEYBOARD_LENOVO_POGO=y', kcfg)

        src_meta = json.loads((ROOT / 'kernel/source.json').read_text(encoding='utf-8'))
        p15 = [p for p in src_meta['kernel']['patches'] if '/0015-' in p]
        self.assertEqual(len(p15), 1)
        p15_text = (ROOT / p15[0]).read_text(encoding='utf-8')
        self.assertIn('lenovo,tb378fc-pogo-keyboard', p15_text)
        self.assertIn('qcom,tx-en-delay-us = <450>;', p15_text)
        self.assertIn('input_mt_sync_frame(tdev);', p15_text)


    def test_consolidated_daemons_zram_recompress_and_snapd_preserved(self):
        tg = load_script('tb378fc_thermal_guard_recomp', 'tb378fc-thermal-guard.py')
        vol = load_script('tb378fc_volume_orientation_tf', 'tb378fc-volume-orientation')

        with tempfile.TemporaryDirectory() as td:
            zdir = Path(td) / 'zram0'
            zdir.mkdir()
            mm_stat = zdir / 'mm_stat'
            idle_f = zdir / 'idle'
            recomp_f = zdir / 'recompress'
            compact_f = zdir / 'compact'
            idle_f.write_text('', encoding='utf-8')
            recomp_f.write_text('', encoding='utf-8')
            compact_f.write_text('', encoding='utf-8')

            recomp_writes = []
            orig_write = tg.write_sysfs

            def track_write(path: Path, value: str) -> bool:
                if path == recomp_f:
                    recomp_writes.append(value)
                return orig_write(path, value)

            with mock.patch.object(tg, 'write_sysfs', side_effect=track_write):
                zr = tg.ZramRecompressor(zram_dir=zdir, interval_sec=120.0, min_orig_bytes=8 * 1024 * 1024)
                # Below threshold: no idle mark, no recompress
                mm_stat.write_text('4096 64 20480 0 20480 0 0 0 0\n', encoding='utf-8')
                self.assertFalse(zr.poll(100.0))
                self.assertFalse(zr.idle_marked)

                # Above threshold (32 MiB): first window marks 'all' idle without recompressing yet
                mm_stat.write_text(f'{32 * 1024 * 1024} 8000000 10000000 0 10000000 0 0 0 0\n', encoding='utf-8')
                self.assertFalse(zr.poll(250.0))
                self.assertTrue(zr.idle_marked)
                self.assertEqual(idle_f.read_text(encoding='utf-8').strip(), 'all')
                self.assertEqual(recomp_writes, [])

                # Next 120s window: recompresses aged idle + huge_idle pages and compacts pool
                self.assertTrue(zr.poll(380.0))
                self.assertEqual(recomp_writes, ['type=idle max_pages=4096', 'type=huge_idle max_pages=1024'])
                self.assertEqual(compact_f.read_text(encoding='utf-8').strip(), '1')

            # Fast tmpfs display-transform lookup without spawning --watch-display
            rdir = Path(td) / 'run-user-1000'
            rdir.mkdir()
            (rdir / 'tb378fc-display-transform').write_text('3\n', encoding='utf-8')
            self.assertEqual(vol.read_session_transform(1000, 1000, str(rdir), {}), 3)

        # Verify systemd consolidation guards, fwupd timed-exit, update-notifier override & snapd preserved
        sym = json.loads((ROOT / 'packaging/device-support/symlinks.json').read_text(encoding='utf-8'))
        self.assertNotIn('etc/systemd/system/graphical.target.wants/tb378fc-power-key.service', sym)
        self.assertNotIn('etc/systemd/system/graphical.target.wants/tb378fc-volume-orientation.service', sym)

        pk_svc = (ROOT / 'packaging/device-support/tree/etc/systemd/system/tb378fc-power-key.service').read_text(encoding='utf-8')
        vol_svc = (ROOT / 'packaging/device-support/tree/etc/systemd/system/tb378fc-volume-orientation.service').read_text(encoding='utf-8')
        sr_svc = (ROOT / 'packaging/audio/tree/usr/lib/systemd/user/tb378fc-speaker-rotation.service').read_text(encoding='utf-8')
        self.assertIn('ConditionPathExists=!/run/tb378fc-system-consolidated', pk_svc)
        self.assertIn('ConditionPathExists=!/run/tb378fc-system-consolidated', vol_svc)
        self.assertIn('ConditionPathExists=!/usr/local/libexec/tb378fc-v4l2-camera-bridge.py', sr_svc)

        fwupd = (ROOT / 'packaging/device-support/tree/etc/systemd/system/fwupd.service.d/90-tb378fc-idle.conf').read_text(encoding='utf-8')
        self.assertIn('--timed-exit', fwupd)

        un_desk = (ROOT / 'packaging/device-support/tree/etc/xdg/xdg-ubuntu/autostart/update-notifier.desktop').read_text(encoding='utf-8')
        self.assertIn('Hidden=true', un_desk)

        bdebs = (ROOT / 'tools/build-debs.py').read_text(encoding='utf-8')
        self.assertIn('unattended-upgrades.service', bdebs)
        self.assertNotIn('disable snapd', bdebs)
        self.assertNotIn('mask snapd', bdebs)

if __name__ == '__main__':
    unittest.main()
