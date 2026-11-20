# Ubuntu for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC)

在联想小新 Pad Pro GT 13（**TB378FC / Snapdragon 8 Elite / SM8750P**）上运行 Ubuntu Desktop 26.04，基于统一主线 **Linux 7.4 (`sm8750/v7.4-dev`)**、模块化 Kconfig、Mesa 26.2.6 (Freedreno / Turnip) 与干净的 `.deb` 打包体系。

本项目基于并同步上游 [OnePlus Pad 2 Pro mainline](https://github.com/yzddmr6/onepluspad-2pro-mainline) (`v0.12.1`) 与 [linux-sm8750-erhai](https://github.com/yzddmr6/linux-sm8750-erhai) (`sm8750/v7.4-dev`) 的 SM8750P 公共内核/图形/启动封装成果，同时**严格隔离所有一加/小米板级驱动**，仅保留 TB378FC 专属板级代码。完整来源见 [NOTICE](NOTICE.md)。

## 架构亮点（v0.2.0 现代化重构）

- **单一统一内核（Linux 7.4-rc2）**：所有 TB378FC 板级改动拆分为 9 个清晰原子补丁（`kernel/patches/0001..0009-*.patch`），合入统一 `Image` + `sm8750-lenovo-tb378fc.dtb` + 标准 `/lib/modules/$(uname -r)`，彻底淘汰旧版 `7.2.0-tb378fc-wifi+` 与 6 个串行外置 `.ko` 热加载服务。
- **原生内核态 60 / 120 / 144Hz 连续切频**：原外置 `refresh-live76` 结构体勾子已正式改写为 `panel-novatek-nt36526.c` + `drm/msm` 原生双缓冲连续 DFPS 补丁。
- **模块化 Kconfig**：废除 5 份 9000 行全量 `.config`，改用 `defconfig` + 上游 `sm8750-{netfilter,peripherals,snap,virt,waydroid}.config` + 精简 `kernel/configs/tb378fc.config`，完整开启 `CONFIG_SECURITY_APPARMOR`、`CONFIG_NF_TABLES`、Rust Binder (Waydroid)、Gunyah 虚拟化、UFS/ext4/CAMSS/Iris/WCD939x/AW88480，并显式关闭 `CONFIG_CRYPTO_DEV_QCE` 与全部非 TB378FC 板级驱动。
- **零单机硬编码与默认安全**：`packaging/device-support/tree/` 移除全部 `pad` / UID `1000` 硬编码（改用 `loginctl` 动态 seat0 会话发现与 `video` 组权限）；`initramfs/init` 默认关闭 `usb-diag` 与 `httpd:8081`，仅在显式传入内核参数 `tb378fc.debug=1` 时启用。
- **Mesa 26.2.6 补丁同步**：同步上游 `v0.12.1` 12 个通用 Adreno 830v1 补丁（含 `freedreno-thread-safe-tex-seqno.patch` 根因修复，移除 `GALLIUM_THREAD=false` 规避项）。
- **安全优先验证流程**：新增 `tools/build-boot-v2.py`（生成 Android header v2 临时 RAM 启动镜像），优先通过 `fastboot boot` 验证后再封装 GKI v4 持久三件套，提供无 root `tools/build-debs.py` 打包脚本与 GitHub Actions CI。

## 文档入口

| 入口 | 内容 |
|---|---|
| [硬件支持](docs/HARDWARE.zh-CN.md) | 硬件模块状态、7.4 迁移清单与已知边界 |
| [构建指南](docs/BUILD.zh-CN.md) | Linux 7.4 内核交叉编译、Mesa、initramfs、boot-v2 与 `.deb` 构建 |
| [双系统、安装与恢复](docs/FLASHING.zh-CN.md) | `fastboot boot` 临时验证、ABL DTBO sink、GKI v4 切换与恢复 |
| [开发说明](docs/DEVELOPMENT.zh-CN.md) | 目录规范、板级隔离红线与离线检查 |
| [验证记录](docs/VALIDATION.zh-CN.md) | 实测范围与构建验收记录 |
| [版本记录](CHANGELOG.md) | 架构演进与更新日志 |

## 源码结构

```text
kernel/       固定上游基线 (source.json)、Rust 工具链锁、9 个板级补丁 (0001..0009)、模块化 tb378fc.config
initramfs/    最小化根分区挂载 (支持 tb378fc.root= 与 opt-in tb378fc.debug=1)
packaging/
  device-support/  无硬编码的 systemd 服务、动态会话助手、udev 规则、symlinks.json
  audio/           AudioReach 拓扑源码 (Lenovo-TB378FC.m4)
  camera/          libcamera TB378FC 补丁
  mesa/            同步自上游 v0.12.1 的 Mesa 26.2.6 Adreno 830v1 补丁与校验锁
  desktop/         Ubuntu 26.04 Desktop ARM64 固定层校验锁
switch/       同活动槽 Ubuntu / Android 切换器与 KernelSU 模块模板
tools/        prepare-kernel.py / build-kernel.sh / build-initramfs.sh / build-boot-v2.py / build-boot-triplet.py / build-debs.py / check-source.py
tests/        device-support 权限与 sysfs 单元测试
firmware/     本机闭源固件自行提取清单（仓库永不附带闭源固件、校准、密钥或原厂备份）
third_party/  固定版本参考工具（AVB 签名与 ABL overlay sink 构建工具）
docs/         中文构建、刷机、硬件与开发文档
```

## 许可与致谢

设备适配与切换器按 GPL-2.0-only 发布；内核、Mesa、libcamera 及第三方参考代码保留各自原有许可，见 [NOTICE](NOTICE.md)。感谢 OnePlus Pad 2 Pro mainline、Y700 社区及 Linux / Mesa / Ubuntu 上游开发者。
