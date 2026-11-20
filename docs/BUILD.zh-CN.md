# 构建指南

## 1. 环境准备（重要：严禁在 Windows NTFS 分区编译内核）

Linux 内核源码包含大小写敏感的同名文件，**必须**放在原生 Linux ext4 文件系统（WSL2 请放在 `~/` 下，切勿放在 `/mnt/c/...`）。建议预留 **40GB+ 空闲磁盘**与 **16GB+ 内存/Swap**。

### 基础编译依赖（Ubuntu 24.04 LTS）

```bash
sudo apt update && sudo apt install -y \
  git build-essential gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
  bc bison flex libssl-dev libelf-dev dwarves device-tree-compiler \
  python3 python3-pip cpio gzip xz-utils zstd kmod openssl \
  e2fsprogs gdisk dpkg-dev shellcheck ccache
```

### 可选：Waydroid Rust Binder 工具链（默认开启 `WITH_WAYDROID=1`）

Linux 7.4-rc2 移除了旧 C Binder，启用 `sm8750-waydroid.config` 需要与 `kernel/rust-toolchain.json` 一致的 Rust 工具链（`rustc 1.85.0`、`bindgen 0.71.1`、`rust-src`、`libclang1-18`）：

```bash
sudo apt install -y libclang1-18 libclang-common-18-dev libllvm18
# 按 kernel/rust-toolchain.json 下载并校验 rustc / rust-std / rust-src / bindgen-cli 后导出：
export RUSTC=/path/to/rust-toolchain/bin/rustc
export BINDGEN=/path/to/rust-toolchain/bindgen/bindgen
export RUST_LIB_SRC=/path/to/rust-toolchain/lib/rustlib/src/rust/library
export LIBCLANG_PATH=/usr/lib/llvm-18/lib
```

若仅做快速 CI 或暂不需要 Waydroid，可通过 `WITH_WAYDROID=0 ./tools/build-kernel.sh` 跳过 Rust 依赖。

### 可选：交叉编译 Mesa（建议 GCC 14）

```bash
sudo apt install -y meson ninja-build pkg-config qemu-user-static \
  gcc-14-aarch64-linux-gnu g++-14-aarch64-linux-gnu
```

> 上游记录 GCC 13 在 `arm64 -O3` 下会误编 Mesa GLSL，交叉编译 Mesa 请使用 **GCC 14**。补丁清单见 [`packaging/mesa/README.md`](../packaging/mesa/README.md)。

---

## 2. 准备并编译统一 Linux 7.4 内核与 DTB

```bash
# 1) 拉取固定基线 (yzddmr6/linux-sm8750-erhai @ sm8750/v7.4-dev) 并应用 9 个 TB378FC 补丁
python3 tools/prepare-kernel.py --kernel-dir ~/tb378fc-linux

# 2) 合并模块化 Kconfig 并交叉编译 Image、sm8750-lenovo-tb378fc.dtb 与全量 modules
KERNEL_SOURCE=~/tb378fc-linux OUT_DIR=~/tb378fc-out/kernel JOBS="$(nproc)" ./tools/build-kernel.sh
```

`tools/build-kernel.sh` 会自动执行：
1. 校验源码不在 NTFS/drvfs 挂载点，且继承自 `kernel/source.json` 固定提交；
2. 合并 `defconfig` + `sm8750-{netfilter,peripherals,snap,virt,waydroid}.config` + `kernel/configs/tb378fc.config`；
3. 断言全部必要选项（含 AppArmor、nf_tables、UFS、ext4、CAMSS、AW88480、NT36526、GC08A8 等）已生效，且**全部一加/小米板级驱动与 `CONFIG_CRYPTO_DEV_QCE` 保持关闭**；
4. 产出 `arch/arm64/boot/Image`、`arch/arm64/boot/dts/qcom/sm8750-lenovo-tb378fc.dtb`、完整 `/lib/modules` 树与 `build-result.json`。

### 9 个模块化内核补丁概览

| 补丁 | 作用 |
|---|---|
| `0001` | CSOT NT36526 双 DSI DSC 面板驱动（适配 7.4 `MIPI_DSI_MODE_DSC_ALL_SLICES_IN_PKT`，原生暴露 60/120/144Hz） |
| `0002` | TB378FC 成对面板偏置稳压器 (`tb378fc-panel-bias`) 与双路背光 (`tb378fc-aw99706b`) |
| `0003` | `drm/msm`：UEFI 扫描输出直接映射保留、最大空闲 32 位 IOVA 窗口、DSI VBIF 优先级、原生无缝 DFPS 切频 |
| `0004` | 四路 AW88480 功放驱动、WCD939x SoundWire IRQ 生命周期与联想原生 ADC 映射、`q6apm-dai` 等待 ADSP 就绪 |
| `0005` | NT36536 SPI 触控 `lenovo,tb378fc-nvt` 变体（压感 4095、严格芯片 ID 校验）与 Trim ID |
| `0006` | `qcom_battmgr` 联想 TB378FC 适配（电流符号、有界 0..900mA USB ICL 写入、只读 `0x60` 充电诊断） |
| `0007` | GC08A8 前摄驱动、OV13B10 联想 VAF/上电时序与 OF 匹配、CAMSS Gen4 256 字节 `sizeimage` 对齐 |
| `0008` | `qcom_geni_serial` 无 `serial*` 别名时钳制负索引，保障蓝牙 UART14 探测 |
| `0009` | 统一版 `sm8750-lenovo-tb378fc.dts` + `sm8750-lenovo-tb378fc-camera.dtsi`（含 118°C CPU 降频、8GB 安全 `ramoops@c2000000`、独立 CSI-2 PHY 相机图、ABL 根身份恢复） |

---

## 3. 构建 Initramfs、临时 `fastboot boot` 镜像与 `.deb` 包

### 3.1 构建最小化 Initramfs

```bash
./tools/build-initramfs.sh \
  --busybox /path/to/busybox-arm64 \
  --gen-init-cpio ~/tb378fc-out/kernel/usr/gen_init_cpio \
  --output ~/tb378fc-out/initramfs.cpio.gz
```

> 默认不启用 `usb-diag` 与 `httpd:8081`；如需调试通道，在启动参数添加 `tb378fc.debug=1`。

### 3.2 优先封装 Header v2 临时 RAM 启动镜像（不触碰任何分区）

注意：在真机上通过 `fastboot boot` 启动前，须先用本机原厂 `vendor_boot`/`dtbo` 为主线 DTB 注入本机专属的 contained ABL overlay sink（使用 `third_party/oneplus/tools/build-erhai-abl-symbols.py`，切勿使用其他设备的符号表）：

```bash
python3 tools/build-boot-v2.py \
  --kernel ~/tb378fc-out/kernel/arch/arm64/boot/Image \
  --dtb ~/tb378fc-out/sm8750-lenovo-tb378fc-abl.dtb \
  --initramfs ~/tb378fc-out/initramfs.cpio.gz \
  --mkbootimg /path/to/mkbootimg.py \
  --root PARTLABEL=linux_rootfs \
  --output ~/tb378fc-out/boot-v2
```

如需开启 USB 诊断与 8081 HTTP 日志端口，追加 `--debug`。

### 3.3 构建无 Root ARM64 `.deb` 安装包

```bash
python3 tools/build-debs.py \
  --version 0.2.0 \
  --kernel-out ~/tb378fc-out/kernel \
  --output ~/tb378fc-out/debs
```

产出 4 个标准 ARM64 `.deb`：
- `tb378fc-device-support_0.2.0_arm64.deb`（动态会话 systemd 服务、脚本、udev、相对符号链接）
- `tb378fc-audio_0.2.0_arm64.deb`（ALSA UCM2 与 `Lenovo-TB378FC.m4` 拓扑源码）
- `tb378fc-kernel-image_<release>-0.2.0_arm64.deb`（`/boot/vmlinuz-*`、DTB 与完整 `/lib/modules/<release>`）
- `tb378fc-device_0.2.0_arm64.deb`（元包）

### 3.4 离线检查与测试

```bash
python3 tools/check-source.py
python3 -m unittest discover -s tests -v
python3 tools/validation/test_switch.py
```
