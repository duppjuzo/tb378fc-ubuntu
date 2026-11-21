# Ubuntu for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)

在联想小新 Pad Pro GT 13（**TB378FC / `zhiyu` / Snapdragon 8 Elite / SM8750P**）上运行 **Ubuntu Desktop 26.04 LTS (`resolute`)**，基于统一主线 **Linux 7.4 (`sm8750/v7.4-dev`)**、模块化 Kconfig、Mesa 26.2.6 (Freedreno / Turnip)、Qualcomm Iris VPU35 VA-API、补丁版 `libcamera 0.7.2` 与干净的 `.deb` / RootFS 构建体系。

本项目基于并同步上游 [OnePlus Pad 2 Pro mainline](https://github.com/yzddmr6/onepluspad-2pro-mainline) (`v0.12.1`) 与 [linux-sm8750-erhai](https://github.com/yzddmr6/linux-sm8750-erhai) (`sm8750/v7.4-dev`) 的 SM8750P 公共内核/图形/启动封装成果，吸收社区 [linux-zhiyu](https://github.com/PickaxeHit/linux-zhiyu) 经硬件验证的休眠、对焦与固件回退修复，同时**严格隔离所有一加/小米板级驱动**，仅保留 TB378FC 专属板级代码。完整来源见 [NOTICE](NOTICE.md)。

## 架构亮点（v0.2.1）

- **单一统一内核（Linux 7.4-rc2，10 个原子补丁 `0001..0010`）**：合入统一 `Image` + `sm8750-lenovo-tb378fc.dtb` + 标准 `/lib/modules/$(uname -r)`，彻底淘汰旧版 `7.2.0-tb378fc-wifi+` 与外置 `.ko` 热加载服务。
- **原生 60 / 90 / 120 / 144 Hz 无缝连续切频**：`panel-novatek-nt36526.c` + `drm/msm` 原生双缓冲 DFPS（四档刷新率压缩 DSI 链路时钟均对齐 ~183 MHz，切换不黑屏）。
- **相机自动对焦 + 补丁版 `libcamera 0.7.2`**：内核集成后摄 **DW9807（GT9772B）VCM 自动对焦**（安全行程限位 `0..400`、仅 Runtime PM 避免休眠弹响镜头）与 `60-persistent-v4l.rules` 防误触发规则；提供 `tools/build-libcamera-deb.py` 交叉构建支持 Adreno EGL debayer 与 CDAF 自动对焦的 `tb378fc-libcamera 0.7.2`。
- **图形、硬解与桌面体验**：提供 `tools/build-mesa-deb.py`（GCC 14 `-O2`，Mesa 26.2.6 Adreno 830/830v1 Freedreno + Turnip）、Iris VPU35 AV1(3×)/10-bit(2×) 时钟缩放 + `tb378fc-iris-vaapi`、四扬声器 `q6apm-dai` 2ch/4ch 声道映射与完整 Electron/Wayland 环境变量。
- **休眠与蓝牙兼容性加固**：补齐 SM8750 TSENS `no_irq_wake`（防降温阈值中断唤醒 `s2idle`）、UCSI `system_freezable_wq`，并为 WCN7860 蓝牙加入 `gngbt*` → 联想原厂 `brhbt*` 自动固件回退。
- **Android Header v4 默认启动与测试流程**：**联想 TB378FC 原厂 ABL 不支持 `fastboot boot`（Header v2 镜像无效）**；统一使用 `tools/build-boot-triplet.py` 直接生成带 AVB 签名的 **GKI v4 启动镜像**（`boot.img` / `init_boot.img` / `vendor_boot.img`，可选 `--with-vbmeta`），在备份原厂三件套后刷入目标槽位验证（仅改内核时后续只需重刷 `boot` 分区）。
- **模块化 Kconfig 与零硬编码**：采用 `defconfig` + 上游 `sm8750-{netfilter,peripherals,snap,virt,waydroid}.config` + 精简 `kernel/configs/tb378fc.config`（AppArmor、nf_tables、Rust Binder、Gunyah VM，显式关闭 `CONFIG_CRYPTO_DEV_QCE` 与全部非 TB378FC 板级驱动）；`packaging/device-support/` 零 `pad`/UID 1000 硬编码；`initramfs/init` 默认关闭调试通道（仅 `tb378fc.debug=1` 启用）。

## 文档入口

| 入口 | 内容 |
|---|---|
| [硬件支持](docs/HARDWARE.zh-CN.md) | 硬件模块状态、7.4 迁移清单与已知边界 |
| [构建指南](docs/BUILD.zh-CN.md) | Linux 7.4 内核、Mesa 26.2.6、libcamera 0.7.2、initramfs、GKI v4 镜像与 Ubuntu 26.04 RootFS 构建 |
| [双系统、安装与恢复](docs/FLASHING.zh-CN.md) | 原厂备份、ABL DTBO sink、GKI v4 刷入测试、双系统切换与紧急恢复 |
| [开发说明](docs/DEVELOPMENT.zh-CN.md) | 目录规范、板级隔离红线与离线检查 |
| [验证记录](docs/VALIDATION.zh-CN.md) | 实测范围与构建验收记录 |
| [版本记录](CHANGELOG.md) | 架构演进与更新日志 |

## 源码结构

```text
kernel/       固定上游基线 (source.json)、Rust 工具链锁、10 个板级补丁 (0001..0010)、模块化 tb378fc.config
initramfs/    最小化根分区挂载 (支持 tb378fc.root= 与 opt-in tb378fc.debug=1)
packaging/
  device-support/  无硬编码的 systemd 服务、动态会话助手、Electron/Wayland 环境、udev 规则、symlinks.json
  audio/           AudioReach 拓扑源码 (Lenovo-TB378FC.m4)
  camera/          libcamera TB378FC 参考补丁
  mesa/            同步自上游 v0.12.1 的 Mesa 26.2.6 Adreno 830v1 补丁与校验锁
  video-defaults/  GStreamer / Firefox / Chromium VA-API 硬解默认配置
  desktop/         Ubuntu 26.04 Desktop ARM64 固定层校验锁
switch/       同活动槽 Ubuntu / Android 切换器与 KernelSU 模块模板
tools/        prepare-kernel.py / build-kernel.sh / build-initramfs.sh / build-boot-triplet.py (Header v4) /
              build-debs.py / build-mesa-deb.py / build-libcamera-deb.py / build-desktop-rootfs.py /
              install-tb378fc-firmware.py / qemu-smoke-test.py / check-source.py
tests/        device-support 权限/配置单元测试与 HIL 真机回归套件 (tests/hil/)
firmware/     本机闭源固件自行提取清单（仓库永不附带闭源固件、校准、密钥或原厂备份）
third_party/  固定版本参考工具（AVB 签名与 ABL overlay sink 构建工具）
docs/         中文构建、刷机、硬件与开发文档
```

## 许可与致谢

设备适配与切换器按 GPL-2.0-only 发布；内核、Mesa、libcamera 及第三方参考代码保留各自原有许可，见 [NOTICE](NOTICE.md)。感谢 OnePlus Pad 2 Pro mainline、linux-zhiyu、Y700 社区及 Linux / Mesa / Ubuntu 上游开发者。
