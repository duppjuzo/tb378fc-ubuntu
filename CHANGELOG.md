# Changelog

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
