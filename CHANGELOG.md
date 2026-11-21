# Changelog

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
