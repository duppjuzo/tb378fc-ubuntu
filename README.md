# Ubuntu for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)

在联想小新 Pad Pro GT 13（**TB378FC / `zhiyu` / Snapdragon 8 Elite / SM8750P**，8GB+256GB）上运行 **Ubuntu Desktop 26.04 LTS (`resolute`)**，提供**跨平台一键刷机向导**与 **ZUXOS ↔ Ubuntu 同活动槽位一键双系统互切**。

本项目基于统一主线 **Linux 7.4-rc2 (`sm8750/v7.4-dev`)**、15 个 TB378FC 原子补丁（`0001..0015`）、Mesa 26.2.6 (Freedreno / Turnip)、Qualcomm Iris VPU35 VA-API、补丁版 `libcamera 0.7.2` 与 `tb378fc-sensors` 传感器栈构建；同步上游 [OnePlus Pad 2 Pro mainline](https://github.com/yzddmr6/onepluspad-2pro-mainline) 与 [linux-sm8750-erhai](https://github.com/yzddmr6/linux-sm8750-erhai) 的 SM8750P 公共成果及 [linux-zhiyu](https://github.com/PickaxeHit/linux-zhiyu) 的休眠/对焦修复，同时**严格隔离所有一加/小米板级驱动**。完整来源见 [NOTICE](NOTICE.md)。

## 快速开始：一键刷机（当前预编译包 v0.6.5 / 源码栈 v0.6.6）

前往本仓库 **Releases** 下载 `v0.6.5` 通用刷机包（`tb378fc-ubuntu-v0.6.5-flash-kit.zip` + 两个 `<2 GiB` 的 `rootfs.squashfs.part00/part01` 分卷）：

1. 解压 `tb378fc-ubuntu-v0.6.5-flash-kit.zip`，将 `.part00` 与 `.part01` 放入解压目录（或 `rootfs/` 子目录）。
2. 平板解锁 Bootloader，在原厂 **ZUXOS**（支持 `_a` 或 `_b` 任意活动槽位）中开启 **USB 调试**，安装 **KernelSU** 并为 `Shell (com.android.shell)` 授予 root 权限。
3. 连接电脑启动一键向导（仅依赖 Python 3.10+ 标准库与 `adb` / `fastboot`，无需 WSL 或额外驱动）：
   - **Windows**：双击 `flash-windows.bat`
   - **Linux / macOS**：终端执行 `./flash-linux.sh`
4. 按菜单选择 **`[1] 首次全新双系统装机`**（自动备份本机原厂分区 → 启动 RAM 环境无损切分 `ubuntu` 分区 → 自动提取本机签名闭源固件 → 写入 Ubuntu 26.04 并激活双系统切换）或 **`[2] 已有分区一键重装`** / **`[3] 不格盘原地升级`**。

> 公开仓库与 Release 刷机包**不包含任何联想/高通闭源受限固件或个人私钥**：所有设备签名固件（`adsp`、`cdsp`、`vpu35_4v.mbn`、`gen80000_zap.mbn`、`aw882xx_acf.bin`、触控与无线固件）均由一键向导在安装时从你自己的平板原厂分区现场自动提取、校验并完成本机 AVB 2.0 签名。

## 核心特性（v0.6.6）

- **同活动槽位一键双系统（`_a` / `_b` 自适应）**：保留原厂未修改 `vbmeta`（`Flags=0`，绝不使用会导致 ZUXOS ABL 拒启的 `--disable-verity` 或 `qbootctl -s`），通过 KernelSU 模块「操作 (Action)」按钮与 Ubuntu 桌面「切换到 Android」图标实现秒级安全互切。
- **3504×2190 原生 60 / 90 / 120 / 144 Hz 无缝切频 + 背光持久化**：合入 VESA DSC 1.2 RC 参数编程补丁（`0011`）与双缓冲 DFPS；修复 GNOME 50 自动感光跨会话累加导致开机跳 100% 亮度的问题，重启持久保留用户设定亮度。
- **完整图形与 H.264 / HEVC / VP9 / AV1 硬解**：Mesa 26.2.6（Adreno 830 Freedreno + Turnip Vulkan）+ `tb378fc-iris-vaapi` 解锁 Iris VPU35 全格式硬解。
- **四扬声器四向随屏旋转 + 双麦录音 + USB-C DP 音视频输出**：AudioReach 拓扑 + AW88480 四功放动态方向路由 + 双路模拟麦克风立体声采集 + USB-C DisplayPort Alt Mode 视频与音频（`0013`）输出。
- **前后摄横屏摆正 + 自动对焦 + V4L2 桌面桥接**：合入横屏 `rotation` 修正（`0012`）、DW9807（GT9772B）安全行程 CDAF 自动对焦、`tb378fc-libcamera 0.7.2` 与 `v4l2loopback` 虚拟摄像头桥接。
- **冷启动即刻触控、GNOME 50 全窗口/Chrome 触屏键盘与磁吸触点键盘驱动**：修复 `nt36536` 冷启动早期固件竞态与 GNOME 50 Wayland 多窗口/Chrome 触控屏幕键盘唤醒；合入联想原厂磁吸触点键盘与四指触控板净室主线驱动（`0015`，平板端 `serdev`/霍尔热插拔已实机就绪，物理键盘吸附待测）。
- **Oryon 4.32 GHz 原生睿频、5K DP 热拔插防死机与功耗管理**：解锁 `schedutil` 4.20/4.32 GHz Prime 睿频（`0014`），修复 4-lane 5K DP 拔线时钟切换死机；`tb378fc-sensors 0.2.2` 提供四向重力旋转、霍尔皮套合盖 `s2idle` 休眠与迟滞光感，配合 MGLRU、zstd zram、UFS MCQ 调优与三档温控持久化。

## 文档入口

| 入口 | 内容 |
|---|---|
| [双系统、一键刷机与恢复](docs/FLASHING.zh-CN.md) | Release 一键刷机流程、首次装机两阶段说明、日常双系统互切与 Fastboot 紧急恢复 |
| [硬件支持状态](docs/HARDWARE.zh-CN.md) | 各硬件子系统驱动、Linux 7.4 补丁矩阵与真机状态 |
| [从源码构建指南](docs/BUILD.zh-CN.md) | 内核、Mesa、libcamera、传感器、RootFS 与通用刷机包完整构建流程 |
| [开发与安全红线](docs/DEVELOPMENT.zh-CN.md) | 目录规范、板级隔离红线与离线校验 |
| [真机验证记录](docs/VALIDATION.zh-CN.md) | 实机验证范围、双系统往返与已知边界 |
| [版本更新日志](CHANGELOG.md) | `v0.1.0` → `v0.6.6` 完整演进记录 |

## 源码结构

```text
kernel/       上游基线锁 (source.json)、Rust 工具链锁、15 个 TB378FC 原子补丁 (0001..0015)、tb378fc.config
initramfs/    最小化早期挂载与可选调试 initramfs (默认支持 PARTLABEL=ubuntu 与 UUID=)
packaging/
  device-support/  systemd 服务、GDM/GNOME 背光与温控策略、动态会话脚本、udev 规则
  audio/           AudioReach 拓扑源码 (Lenovo-TB378FC.m4) 与 ALSA UCM2 配置
  sensors/         高通 ADSP 传感器守护进程 (tb378fc-sensord.c，重力旋转 / 皮套霍尔 / 迟滞光感)
  camera/          libcamera 0.7.2 TB378FC 补丁与 CDAF 对焦配置
  mesa/            Mesa 26.2.6 Adreno 830 补丁与构建锁
  video-defaults/  GStreamer / Firefox / Chromium VA-API 硬解默认环境
  desktop/         Ubuntu 26.04 Desktop ARM64 固定层校验锁
switch/       同活动槽 (_a / _b) Ubuntu ↔ Android 切换器与 KernelSU 模块模板
tools/
  flash-tb378fc.py       纯 Python 跨平台一键刷机、本地 AVB 签名、本机固件提取与双系统向导
  build-flash-bundle.py  标准通用刷机包与 GitHub Release 分卷资产构建/隐私审计工具
  gpt-split/             4K 扇区 UFS GPT 无损缩容分区工具 (tb378fc-gpt-split.c)
  build-setup-initramfs.sh / build-initramfs.sh / build-boot-triplet.py / build-kernel.sh
  build-debs.py / build-sensors-deb.py / build-mesa-deb.py / build-libcamera-deb.py / build-desktop-rootfs.py
tests/        离线源码卫生、AVB 签名、DTBO 符号覆盖、GPT 切分、音频切片与系统配置单元测试
```
