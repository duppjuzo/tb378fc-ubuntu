# Changelog

## 0.6.10 — 2026-11-26

### `s2idle` 休眠修复与短按电源键自动休眠（`tb378fc-device-support 0.6.10` + `tb378fc-sensors 0.2.5`）
- **修复 `tb378fc-power-key.py` 锁屏 D-Bus 阻塞与连按竞争**：将阻塞式 `org.gnome.ScreenSaver.Lock` 改为非阻塞 `loginctl lock-session`（3s 超时）+ 2.5s 轮询 `GetActive`/`LockedHint`（1s 后 `SetActive true` 兜底），超时仍强制熄屏并记录日志；新增按后排空缓冲输入与 0.6s 连按防抖，消除 `8s Lock timed out` 与快速切换导致的 `nt36536 -112` 告警。
- **修复 `console-setup` systemd 单元转义告警**：将 `90-tb378fc-sanitize-xkb.conf` 中 `\1` 反向引用转义为 `\\1`。
- **消除 PCIe `IRQ 218` 虚假秒醒并修复 DTS `wake-gpios` 极性**：将 `0009` 补丁及内核源码 `&pcieport0` 的 `wake-gpios` 由 `GPIO_ACTIVE_HIGH` 更正为 `GPIO_ACTIVE_LOW`；同时通过 udev 规则 `90-tb378fc-pcie-wakeup.rules` 在现网启动阶段禁用 `0000:00:00.0` `power/wakeup`。
- **消除 `s2idle` 期间 ADSP 5 Hz 唤醒风暴（`tb378fc-sensors 0.2.5`）**：在 `sensh` 中将连续传感器配置流（`msg_id == 513`，加速度计/环境光）设为 `SNS_CLIENT_DELIVERY_NO_WAKEUP (1)`，仅保留 `watch`（`514`，磁吸霍尔开关）及查询类消息为 `WAKEUP (0)`。
- **开启短按电源键自动进入 `s2idle`**：默认写入 `/etc/tb378fc/power-key-action` = `suspend`。
- **启用 SM8750 SoCCP 电源控制器并固化 PCIe `WAKE#` 极性（补丁 `0016`、`vendor_boot_b` 真机验证）**：在 `sm8750-lenovo-tb378fc.dts` 启用 `&smp2p_soccp` 与 `&remoteproc_soccp`（`qcom/sm8750/tb378fc/soccp.mbn`）并将 `&pcieport0` `wake-gpios` 移入独立补丁 `0016` 修正为 `GPIO_ACTIVE_LOW`（同步还原 `0009` 基线并更新固件安装脚本纳入 `soccp.mbn`）；经真机刷入 `vendor_boot_b` 重启验证，`remoteproc0` 已加载原厂 `soccp.mbn` 并进入 D3 释放引导阶段 NoC 投票（`in D3, boot bus vote dropped`），`s2idle`（插线状态）可稳定睡眠并由 RTC 干净唤醒（`fail: 0`），`cxsd`/`ddr` 塌陷仍待后续拔线及 `dwc3-qcom` HS-PHY / `qcom-pcie` L2 排查。
- **禁用未用 TZ-owned `&crypto` 与 `&lpass_wsa*` 节点并真机打通 `cxsd`/`ddr` 深度塌陷（补丁 `0017`、`vendor_boot_b` 真机验证）**：经真机全量排查确认 `1dfa000.crypto`（TZ 接管、内核无驱动）阻塞 `interconnect-1`（`mc_virt`：`MC0`/`ACV`）与 `1700000.interconnect`（`aggre2_noc`：`CE0`/`SN0`）完成 `sync_state()`，导致 DDR 频率锁死在 3187 MHz 无法降频与自刷新；新增补丁 `0017` 在 `sm8750-lenovo-tb378fc.dts` 禁用 `&crypto` 以及未使用的 `&lpass_wsamacro`/`&lpass_wsa2macro`（TB378FC 扬声器使用 I2C `AW88480` 走 TDM）。刷入更新后 `vendor_boot_b` 重启实测全系统 `state_synced == 0` 设备已清零；插着 USB 执行 25 秒 `rtcwake -m mem -s 25`：`qcom_stats/cxsd` `0 -> 53`（+24.36 秒 / ~97.4%）、`qcom_stats/ddr` `0 -> 53`（+24.39 秒 / ~97.6%）、`ddr_stats` `0xd4`（DDR 自刷新）`0 -> 53`（+24.40 秒），`suspend_stats` `success=1, fail=0`（`pm_wakeup_irq=21` RTC 唤醒）。
## 0.6.6 — 2026-11-24

### 联想原厂磁吸触点键盘与多指触控板内核支持（补丁 `0015`）
- **半双工 `qcom_geni_serial` 与 `lenovo_pogo_keyboard` 净室主线驱动（`CONFIG_KEYBOARD_LENOVO_POGO=y`）**：
  - 为 `drivers/tty/serial/qcom_geni_serial.c` 增加可选半双工 `tx-en-gpios`（`gpio29`）与 `qcom,tx-en-delay-us = <450>` 发送前建立延时及发送完成立即切回 RX 支持，并通过 `dt-schema` 校验。
  - 新增 `drivers/input/keyboard/lenovo_pogo_keyboard.c`（`serdev` 驱动，挂载于 `0xa88000` `QUPv3 WRAP1 SE2` @ `921600 8N1`），完整实现 PPP 转义帧解析、64 字节旋转 XOR 解扰、6KRO 标准按键、多媒体/背光快捷键、4 指 `INPUT_MT_POINTER` 触控板（含 `input_mt_sync_frame()` 指针仿真与防误触）、Caps/Mute/MicMute/FnLock 指示灯双向同步、霍尔热插拔（`gpio31`）、供电开关（`gpio81`）、唤醒中断（`gpio87`）及 `drm_panel_follower` 联动；同时严守板级隔离红线，保持 `CONFIG_KEYBOARD_ONEPLUS_POGO` 禁用。
  - *验证状态*：离线协议单测、`checkpatch.pl`、`dt-doc-validate` 与内核 `-Werror` 编译全部通过；真机（未吸附键盘状态）已验证 `ttyHS1`、霍尔/唤醒中断（`IRQ 210/211`）、sysfs 属性与断电休眠逻辑正常，物理磁吸键盘实机输入联调待后续合入验证记录。

### 冷启动触控修复、外接 5K DP 拔线防死机与 Oryon 4.32 GHz 睿频（补丁 `0014`）
- **修复冷启动偶发触摸失灵（无需息屏重亮）**：将 `nt36536` 的 `panel_follower` 唤醒固件加载改造为 `delayed_work` + `firmware_request_nowarn()`，在早期 initramfs 尚未挂载根分区 `/lib/firmware/novatek/tb378fc.bin` 时自动按 500 ms 延迟重试直至 rootfs 就绪，并在内存中缓存已解析固件避免后续亮屏重复读盘校验；真机冷重启验证 `0` 报错、进桌面即刻响应触控。
- **修复外接 5K/4-lane DisplayPort 显示器热拔插重启**：在 `&usb` 节点补齐 `mode-switch;`，使 4-lane DP Alt Mode 拔出时 DWC3 先切回 UTMI 时钟再关闭 QMP `pipe_clk`，消除 xHCI/DWC3 挂死引发看门狗重启；为 `msm_dp_audio_shutdown()` 增加掉电状态守卫，并禁用未使用的 `tn@109ab000`。
- **解锁原生 `schedutil` 4.20 / 4.32 GHz Oryon Prime 睿频**：修正 `schedutil` 在 `boost_enabled` 激活且 `policy->max` 高于持续基准频率时的利用率映射上限，配合三档 `tb378fc-thermal-guard`（省电 3.53 GHz / 平衡与性能自动睿频至 **4.32 GHz**）实机达成满血单核睿频。
- **内核日志降噪与 PMIC/音频细节打磨**：静默 `btqca` 可选固件探测告警、为 `lenovo,tb378fc-pmic-glink` 标记 `QCOM_BATTMGR_QUIRK_NO_CHARGE_CONTROL` 消除不支持的 `0x48` 报错，并补齐 `wcd939x` MBHC 阈值与 `lpass_vamacro` 采样率属性。

### 自动旋转修复（Mutter `50.1-0ubuntu2+tb378fc2` + `tb378fc-sensors 0.2.4`）
- **修复冷启动触控就绪后桌面自动旋转静默失效（`mutter 50.1-0ubuntu2+tb378fc2`）**：在 `packaging/desktop/patches/mutter-tb378fc-scaled-mirror-and-autorotate.patch` 合入上游 `GNOME/mutter!4962`（`25e48d8b`）`sync_orientation_unmanaged_inhibit()` 修复，避免 `panel_orientation_managed` 在首次方向读取前先变为 `TRUE` 时引发 `MetaOrientationManager.inhibited_count` 下溢为 `-1`，导致 Mutter 永远不向 `SensorProxy` 调用 `ClaimAccelerometer`。
- **补齐 `SensorProxy/Compass` D-Bus 存根与认领日志（`tb378fc-sensors 0.2.4`）**：`tb378fc-sensord.c` 新增 `/net/hadess/SensorProxy/Compass`（`HasCompass=false`）使无参数 `monitor-sensor` 正常进入加速度/光照监听流，并在 `ClaimAccelerometer` / `ReleaseAccelerometer` 记录认领客户端日志。

### GNOME 50 多窗口 / Chrome 触控屏幕键盘与全栈桌面优化（`0.6.6`）
  - **根治 GNOME 50 自动屏幕亮度暴冲与暗室降不下去的问题（`tb378fc-sensors 0.2.3` + `brightnessManager.js`）**：`tb378fc-sensord.c` 保持 100% 真实物理 `lux` 输出并修复 `0.0 lux` 过渡漏报与全黑静默逻辑；新增 GNOME Shell `/org/gnome/shell/misc/brightnessManager.js` 原生自动亮度控制器覆盖，旁路 `gsd-power` 存在缺陷的单点线性归一化（`norm = 1.5 * lux`）与 `<= 0.0 lux` 丢弃逻辑，改用针对双路 `AW99706B`（`40..4095`）标定的绝对物理照度对数插值 LUT、Android 级非对称防抖（升亮 1.8s 抗白屏/眩光突变、降亮 0.9s 防手影）与独立 30 Hz 平滑渐变时钟，并修复四向旋转（`_monitorsChanged`）篡改亮度偏置的棘轮缺陷。
- **彻底修复 GNOME 50 多窗口与 Chrome/Chromium 触屏无法呼出屏幕键盘（OSK）**：
  - 重写 `packaging/device-support/tree/usr/local/share/tb378fc/gnome-shell/ui/keyboard.js` 触控焦点追踪：同时在 `global.stage` 与 `Main.uiGroup` 捕获触屏轻点事件（覆盖位于 `global.window_group` 的普通桌面客户端窗口），兼容单次 `zwp_text_input_v3.enable()` 会话模型，并修复连接蓝牙键鼠外设时触屏点击仍可自动弹出虚拟键盘。
  - 在 `/etc/chromium.d/tb378fc-wayland-vaapi` 与新增 `/usr/local/libexec/tb378fc-sync-chrome-vaapi`（含 APT `DPkg::Post-Invoke` 钩子）自动注入 `--enable-wayland-ime --wayland-text-input-version=3`，使 Google Chrome / Chromium 原生走 Wayland Text Input v3 协议唤醒虚拟键盘。
- **启动提速、存储/内存调优、蓝牙高清音频与 Iris VA-API 更新**：
  - **常驻内存深度精简（保留 `snapd` 与应用中心）**：将板级 6 个常驻 Python 进程收敛为 **1 个系统守护进程 + 1 个用户会话守护进程**（`tb378fc-thermal-guard` 进程内托管电源键与无子进程音量键旋转路由，`tb378fc-v4l2-camera-bridge` 进程内托管四扬声器旋转并共享 `$XDG_RUNTIME_DIR/tb378fc-display-transform`）；为 `fwupd.service` 配置 `--timed-exit` 按需唤醒后自动退出，停用常驻空等信号的 `unattended-upgrades.service` 与后台常驻 `update-notifier`；并在 `ThermalGuardDaemon` 内置每 120 秒零子进程 `/sys/block/zram0/recompress` 冷页 `lz4 -> zstd` 二级重压缩与 `compact` 内存池紧缩。
  - `tools/build-initramfs.sh` 精简早期 initramfs 固件（仅保留 `regulatory.db*`），避免根分区挂载前 Adreno 830 提前抢跑触发约 5 秒 GMU OOB 超时。
  - 新增 `tb378fc-hw-init` UFS 4.0 MCQ 队列与缓存调优、`90-tb378fc-mglru.conf`、4 GiB `zstd` zram 与 `90-tb378fc-vm.conf` 低延迟内存回收策略。
  - 新增 WirePlumber `51-tb378fc-bluetooth.conf`（优先启用 AAC / LDAC / SBC-XQ 高码率蓝牙音频）与 `CONFIG_BT_AOSPEXT=y`；`tb378fc-iris-vaapi` 升级至 `0.4.0+git.4125b11-4`（新增 `0007` Chromium VA-API 编码兼容补丁）；合入 `sm8750-waydroid.config` 内核配置片段与国内 NTP 源兜底。

## 0.6.5 — 2026-11-23

### 标准通用一键刷机包与跨平台全流程向导
- 新增纯 Python 3.10+ 零外部依赖跨平台一键刷机向导 `tools/flash-tb378fc.py`（Windows `flash-windows.bat` / Linux & macOS `flash-linux.sh`），内置纯 Python AVB 2.0 `SHA256_RSA4096` 签名器、FDT/DTBO `__fixups__` 符号 100% 覆盖预检、AW88480 Music ACF 切片器与备份只读保护，覆盖首次自动分区装机、已有分区一键重装、不格盘原地升级与 Fastboot 紧急恢复四大模式。
- 新增 4K 扇区 UFS GPT 无损缩容工具 `tools/gpt-split/tb378fc-gpt-split.c` 与自动分区 RAMdisk 构建脚本 `tools/build-setup-initramfs.sh`（支持 `tb378fc.autosplit=<GiB>` 免驱动自动切分回退 Fastboot）。
- 新增通用刷机包打包与隐私/闭源固件红线审计工具 `tools/build-flash-bundle.py`，自动生成 `<2 GiB` 分卷纯净 RootFS SquashFS 与 GitHub Release 资产包。

### 同活动槽位（`_a` / `_b`）双系统与桌面/背光体验修复
- **GNOME 50 开机亮度持久化与光感迟滞滤波（`0.6.5` / `tb378fc-sensors 0.2.2`）**：新增 `90_tb378fc-power.gschema.override` 与 GDM 50 `90-tb378fc-greeter` 锁定策略，彻底修复自动亮度跨 GDM→桌面会话累加导致每次开机跳 100% 亮度的问题（保留设置面板手动自动亮度开关）；将 `tb378fc-sensord` 光照阈值升级为相对迟滞 `fmax(2.0, light * 0.08)`，消除空闲态 5 Hz `systemd-logind` 唤醒。
- **Slot `_a` / `_b` 同槽双系统支持**：`switch/tb378fc-switch` 与 `tools/package-switch.py` 支持配置 `TB378FC_SLOT=_a|_b`，适配 OTA 升级至 `_b` 槽的 `ZUXOS 2.0.10.320` 双向一键互切，确立保留未修改原厂 `vbmeta` (`Flags=0`) 红线并彻底禁用 `qbootctl -s`。
- **内核补丁扩展至 13 项（`0001..0013`）及多媒体全栈完善**：合入 VESA DSC 1.2 RC 参数编程（`0011`）、前后摄横屏旋转属性（`0012`）、USB-C DisplayPort0 音频输出（`0013`）、MGLRU + UFS HWMON + `v4l2loopback` 虚拟摄像头桥接、Iris VPU35 AV1 VA-API 硬解解锁、四扬声器四向随屏旋转及 6W 闭环功耗墙/温控档位持久化。

## 0.2.2-v7.4-audio-sensors-audit — 2026-11-22

### 双麦克风录音、四扬声器随屏旋转与磁吸皮套/传感器全栈合并
- **双路 WCD9390 麦克风修复（补丁 `0004` + `0009` + UCM2 + AudioReach 拓扑）**：
  - 修复 `drivers/soundwire/qcom.c` 端口分配 `pn >= maxport` 越界拒绝第 4 端口（DP4）导致 `-EBUSY` 的问题，以及 `wcd939x-sdw.c` `ch_count = 1` 通道计数多 1 的上游缺陷。
  - 移除错误的 `tb378fc_native_tx_ch_info` 覆盖，对齐主线 WCD9390 硬件复位 Slave Port 1 映射（`ADC1=SWR_MIC4`、`ADC3=SWR_MIC6`），修正 DTS `AMIC4 -> MIC BIAS4` 偏置供电与 `&swr2` 19.2 MHz 总线 DP3/DP4 时序（`lane 1/off 0`、`sinterval 3`），并在 `Mic.conf` 配置 `+18 dB` 抽取增益（`102`）与 `75 Hz` 高通滤波。
  - 在 `tools/build-debs.py` 构建阶段使用 `m4` + `alsatplg` 自动编译并反编译校验 `/lib/firmware/qcom/sm8750/Lenovo-TB378FC-tplg.bin`（同时包含 4 声道播放与双麦录音子图），补齐缺失的 `/usr/share/alsa/ucm2/conf.d/sm8750/Lenovo-TB378FC.conf` 发现软链。
- **四扬声器随屏自动旋转**：在 `tb378fc-audio` 集成基于 `org.gnome.Mutter.DisplayConfig` 与 PipeWire 4 声道 `filter-chain` 的 `tb378fc-speaker-rotation` 用户态服务，横竖屏四向旋转时自动切换左右物理声道。
- **磁吸皮套休眠唤醒 + 四向屏幕自动旋转（`tb378fc-sensors`）**：新增 `tools/build-sensors-deb.py`，交叉编译打包 `hexagonrpcd`、`libqrtr`、`sensh` 与 `tb378fc-sensord`，支持磁吸保护套霍尔开关 `SW_LID` 合盖休眠/开盖唤醒，以及 `net.hadess.SensorProxy` 四向重力感应自动旋转与环境光上报。

### 历史代码全量审计与隐患清零
- **AW88480 功放固件自动切片**：在 `tb378fc-install-firmware` 内置原厂 `aw882xx_acf.bin` → `/lib/firmware/awinic/tb378fc/aw88480-native-music.acf` 无损单 Profile（Music）提取与 CRC8/寄存器边界校验，并自动部署 `/vendor/etc/sensors` 至 `/usr/share/qcom/sensors`。
- **清理 RAM 试验阶段残留配置**：移除阻断皮套合盖休眠的 `HandleLidSwitch=ignore`、导致重启丢日志的 `Storage=volatile`、悬空的旧传感器单元与死文件 `camera75/`，替换为正式桌面版 `90-tb378fc-{logind,journald,watchdog}.conf`、NetworkManager MAC 保持配置与 Chrony RTC 恢复配置。

## 0.2.1-v7.4-gki-v4 — 2026-11-21

### 启动流程切换为 Android Header v4 默认主线
- 因联想 TB378FC 原厂 ABL 不支持 `fastboot boot`（Header v2 镜像无效），将 `tools/build-boot-triplet.py` 升级为默认主启动镜像工具，支持 `--stock` 原厂匹配签名（保留原厂 `vbmeta`）、独立固定分区尺寸 AVB 签名、`--with-vbmeta`（`--flags 3`）及 `--unsigned` 离线校验模式，并自动检查 DTB 是否已注入 ABL overlay sink。
- 将 `tools/build-boot-v2.py` 标记为遗留可选工具，全面更新 `README.md`、`docs/FLASHING.zh-CN.md`、`docs/BUILD.zh-CN.md`、`docs/HARDWARE.zh-CN.md`。

### 内核、相机、蓝牙与 Ubuntu 26.04 RootFS 增强
- 扩展至 10 个模块化补丁（`0001..0010`）：新增原生 90 Hz DFPS 模式（60/90/120/144 Hz 同 ~183 MHz 链路时钟）、`q6apm-dai` 2ch/4ch 播放声道映射、后摄 DW9807（GT9772B）VCM 自动对焦与 DT Schema、Iris VPU35 AV1(3×)/10-bit(2×) 时钟缩放、SM8750 TSENS `no_irq_wake`、UCSI `system_freezable_wq` 与 WCN7860 `gngbt*` → `brhbt*` 自动固件回退。
- 新增 `tools/build-libcamera-deb.py`（补丁版 `libcamera 0.7.2`，支持 Adreno EGL debayer 与 CDAF 自动对焦）、`60-persistent-v4l.rules`、`dma_heap` `uaccess` 规则与 `tb378fc-install-firmware` 对 `brhbt*`/`novatek_ts_fw.bin` 的支持。

## 0.2.0-v7.4-modernize — 2026-11-20

### 阶段 P0：工程清理、去硬编码与安全加固
- 移除 `packaging/device-support/tree/` 中全部 `pad` / UID `1000` 硬编码：`tb378fc-volume-orientation` 改用 `loginctl` 动态 seat0 图形会话发现；`tb378fc-torch.socket` 改为 `video` 组 (`0660`) 与动态 peer 鉴权。
- 清理重复的 `.desktop`、统一 `symlinks.json` 为相对路径、补齐缺失的 `tb378fc-suspend-cycle` 休眠钩子、移除全部 6 个 `7.2.0-tb378fc-wifi+` 外置 `.ko` 串行加载服务与旧 `GALLIUM_THREAD=false` 规避项。
- 将 `initramfs/init` 的 `usb-diag` 与 `httpd:8081` 调试通道改为内核参数 `tb378fc.debug=1` 显式开启，并支持 `tb378fc.root=` 与标准 ext4/overlay 根布局。
- 清理 315 个历史 `port/` 脚本与冗余 `snapshots/`（有效资产已迁入 `packaging/audio/`、`packaging/camera/`、`initramfs/`，历史保留于 Git）；改写 `tb378fc-gpu-monitor.py` 直接读取标准 devfreq/runtime-PM sysfs。
- 新增 `tests/test_device_support.py` 单元测试与强化版 `tools/check-source.py` 回归护栏。

### 阶段 P1：迁移 `sm8750/v7.4-dev`、内核大一统与构建现代化
- 将全部 TB378FC 板级代码拆分为 9 个原子补丁（`kernel/patches/0001..0009-*.patch`），完整移植至 `yzddmr6/linux-sm8750-erhai` (`sm8750/v7.4-dev` / Linux `7.4.0-rc2` `da4b783e34bd`)。
- 将外置 `refresh-live76` 结构体 hack 重写为 `panel-novatek-nt36526.c` + `drm/msm` 原生双缓冲连续 60/120/144Hz DFPS 补丁；适配 7.4 `MIPI_DSI_MODE_DSC_ALL_SLICES_IN_PKT` 与独立 CSI-2 PHY 相机图谱。
- 统一 `sm8750-lenovo-tb378fc.dts` + `sm8750-lenovo-tb378fc-camera.dtsi`：内置 UFS、PM8550 闪光灯、SM8750P 118°C CPU 被动降频、8GB 安全 `ramoops@c2000000` (4 MiB) 与 `CONFIG_QCOM_ABL_ROOT_IDENTITY`。
- 废除 5 份 9000 行全量 `.config`，改用上游 5 个模块化 `sm8750-*.config` 片段（AppArmor、nf_tables、Waydroid Rust Binder、Gunyah VM）+ 精简 `kernel/configs/tb378fc.config`，显式关闭 `CONFIG_CRYPTO_DEV_QCE` 与全部非 TB378FC 板级驱动。
- 同步上游 `v0.12.1` 12 个通用 Mesa 26.2.6 Adreno 830v1 补丁（含 `freedreno-thread-safe-tex-seqno.patch`）。
- 新增 `tools/build-kernel.sh`、`tools/build-initramfs.sh`、`tools/build-boot-v2.py`（优先 `fastboot boot` 验证）、无 root `tools/build-debs.py` 与 `.github/workflows/ci.yml`；升级 `tools/build-boot-triplet.py` 默认干净 cmdline。

## 0.1.0-source — 2026-10-19

- 发布 TB378FC / SM8750P 的实际移植源码和 Linux 7.2 平台修改。
- 整理最终设备树、内核配置、相机、SoundWire 音频、蓝牙 UART、UFS、刷新率模块与桌面服务。
- 记录 3504×2190 内屏、60/120/144Hz、上升沿触摸中断、四向旋转、GPU 与视频短测结果。
- 记录 PPS 40～45W、USB-C 外屏与 USB 鼠标、扬声器、第一路麦克风及前后摄实测。
- 完成 80GiB Ubuntu 内置安装和 ColorOS 往返，持久设置保留；修复 GKI v4 OS 元数据、Snap 服务和管理员账户配置。
- 提供参考一加项目的同活动槽切换代码、KernelSU 模块打包和离线测试。
- 源码发布不含个人根文件系统、密钥、密码、配对、网络配置或设备校准。
