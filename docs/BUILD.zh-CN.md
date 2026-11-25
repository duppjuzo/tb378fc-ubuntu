# 构建指南

## 1. 环境准备（重要：严禁在 Windows NTFS 分区编译内核）

Linux 内核源码包含大小写敏感的同名文件，**必须**放在原生 Linux ext4 文件系统（WSL2 请放在 `~/` 下，切勿放在 `/mnt/c/...`）。建议预留 **40GB+ 空闲磁盘**与 **16GB+ 内存/Swap**。

### 基础编译依赖（Ubuntu 24.04 LTS）

```bash
sudo apt update && sudo apt install -y \
  git build-essential gcc-aarch64-linux-gnu g++-aarch64-linux-gnu \
  bc bison flex libssl-dev libelf-dev dwarves device-tree-compiler \
  python3 python3-pip cpio gzip xz-utils zstd kmod openssl \
  e2fsprogs gdisk dpkg-dev shellcheck ccache squashfs-tools
```

### Waydroid Rust Binder 工具链（默认开启 `WITH_WAYDROID=1`）

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

### 交叉编译 Mesa 26.2.6 与 libcamera 0.7.2（建议 GCC 14）

```bash
sudo apt install -y meson ninja-build pkg-config qemu-user-static \
  gcc-14-aarch64-linux-gnu g++-14-aarch64-linux-gnu
```

> 上游记录 GCC 13 在 `arm64 -O3` 下会误编 Mesa GLSL，交叉编译 Mesa 与 libcamera 请使用 **GCC 14**。Mesa 补丁清单见 [`packaging/mesa/README.md`](../packaging/mesa/README.md)。

---

## 2. 准备并编译统一 Linux 7.4 内核与 DTB

```bash
# 1) 拉取固定基线 (yzddmr6/linux-sm8750-erhai @ sm8750/v7.4-dev) 并应用 10 个 TB378FC 补丁
python3 tools/prepare-kernel.py --kernel-dir ~/tb378fc-linux

# 2) 合并模块化 Kconfig 并交叉编译 Image、sm8750-lenovo-tb378fc.dtb 与全量 modules
KERNEL_SOURCE=~/tb378fc-linux OUT_DIR=~/tb378fc-out/kernel JOBS="$(nproc)" ./tools/build-kernel.sh
```

`tools/build-kernel.sh` 会自动执行：
1. 校验源码不在 NTFS/drvfs 挂载点，且继承自 `kernel/source.json` 固定提交；
2. 合并 `defconfig` + `sm8750-{netfilter,peripherals,snap,virt,waydroid}.config` + `kernel/configs/tb378fc.config`；
3. 断言全部必要选项（含 AppArmor、nf_tables、UFS、ext4、CAMSS、AW88480、NT36526、GC08A8、DW9807 VCM 等）已生效，且**全部一加/小米板级驱动与 `CONFIG_CRYPTO_DEV_QCE` 保持关闭**；
4. 产出 `arch/arm64/boot/Image`、`arch/arm64/boot/dts/qcom/sm8750-lenovo-tb378fc.dtb`、完整 `/lib/modules` 树与 `build-result.json`。

### 10 个模块化内核补丁概览

| 补丁 | 作用 |
|---|---|
| `0001` | CSOT NT36526 双 DSI DSC 面板驱动（适配 7.4 `MIPI_DSI_MODE_DSC_ALL_SLICES_IN_PKT`，原生暴露 **60/90/120/144 Hz** 同链路时钟模式） |
| `0002` | TB378FC 成对面板偏置稳压器 (`tb378fc-panel-bias`) 与双路背光 (`tb378fc-aw99706b`) |
| `0003` | `drm/msm`：UEFI 扫描输出直接映射保留、最大空闲 32 位 IOVA 窗口、DSI VBIF 优先级、原生无缝 DFPS 切频 |
| `0004` | 四路 AW88480 功放驱动、WCD939x SoundWire IRQ 生命周期与联想原生 ADC 映射、`q6apm-dai` 2ch/4ch 声道映射与 ADSP 就绪延迟探测 |
| `0005` | NT36536 SPI 触控 `lenovo,tb378fc-nvt` 变体（压感 4095、严格芯片 ID 校验）与 Trim ID |
| `0006` | `qcom_battmgr` 联想 TB378FC 适配（电流符号、有界 0..900mA USB ICL 写入、只读 `0x60` 充电诊断与 DT Schema） |
| `0007` | GC08A8 前摄驱动、OV13B10 联想 VAF/上电时序与 OF 匹配、**DW9807（GT9772B）VCM 自动对焦**与 DT Schema、CAMSS Gen4 256B `sizeimage` 对齐、**Iris VPU35 AV1(3×)/10-bit(2×) 时钟缩放** |
| `0008` | `qcom_geni_serial` 无 `serial*` 别名时钳制负索引，保障蓝牙 UART14 探测 |
| `0009` | 统一版 `sm8750-lenovo-tb378fc.dts` + `sm8750-lenovo-tb378fc-camera.dtsi`（含 118°C CPU 降频、8GB/12GB 通用安全 `ramoops@c2000000`、独立 CSI-2 PHY 相机图 + `lens@c` VCM、ABL 根身份恢复） |
| `0010` | SM8750 TSENS `no_irq_wake`（防降温阈值唤醒 `s2idle`）、UCSI `system_freezable_wq`、WCN7860 蓝牙 `gngbt*` → `brhbt*` 自动固件回退 |

---

## 3. 构建 Initramfs、Android Header v4 启动镜像、`.deb` 包与 RootFS

### 3.1 构建最小化 Initramfs

```bash
./tools/build-initramfs.sh \
  --busybox /path/to/busybox-arm64 \
  --gen-init-cpio ~/tb378fc-out/kernel/scripts/gen_init_cpio \
  --output ~/tb378fc-out/initramfs.cpio.gz
```

> 默认不启用 `usb-diag` 与 `httpd:8081`；如需调试通道，构建 v4 镜像时传入 `--debug`（写入 `tb378fc.debug=1`）。

### 3.2 构建 Android Header v4 启动镜像（默认主路线）

> **注意**：联想 TB378FC 原厂 ABL 不支持 `fastboot boot`（Header v2 镜像无法启动），请统一使用 `tools/build-boot-triplet.py` 生成 **Header v4** 镜像刷入目标槽位。上机前须先用本机原厂 `vendor_boot`/`dtbo` 为主线 DTB 注入本机专属的 contained ABL overlay sink（`third_party/oneplus/tools/build-erhai-abl-symbols.py`）。

```bash
# 同 A 槽三件套（推荐：读取本机原厂分区尺寸与 rollback，不改动 vbmeta）
python3 tools/build-boot-triplet.py \
  --kernel ~/tb378fc-out/kernel/arch/arm64/boot/Image \
  --dtb ~/tb378fc-out/sm8750-lenovo-tb378fc-abl.dtb \
  --initramfs ~/tb378fc-out/initramfs.cpio.gz \
  --stock /path/to/private/stock-triplet \
  --key /path/to/private/avb-rsa4096.pem \
  --mkbootimg /path/to/mkbootimg.py \
  --avbtool /path/to/avbtool.py \
  --root UUID=<你的ubuntu根分区UUID> \
  --output ~/tb378fc-out/v4-triplet
```

- 首次排障可加 `--debug` 启用 `tb378fc.debug=1` USB 诊断通道；
- 若省略 `--stock` 并追加 `--with-vbmeta`，脚本将按 TB378FC 实测容量（96/8/96 MiB）生成独立 AVB 签名三件套及 `--flags 3` 的 `vbmeta.img`；
- 仅离线检查 Header v4 结构可加 `--unsigned`（无需私钥，不可直接刷机）。

### 3.3 构建全套 ARM64 `.deb` 安装包与 Ubuntu 26.04 桌面 RootFS

```bash
# 基础 4 个 .deb（device-support / audio / kernel-image / device 元包）
python3 tools/build-debs.py \
  --version 0.2.0 \
  --kernel-out ~/tb378fc-out/kernel \
  --output ~/tb378fc-out/debs

# 针对 Ubuntu 26.04 (resolute) sysroot 交叉编译 Mesa 26.2.6 与补丁版 libcamera 0.7.2
python3 tools/build-mesa-deb.py --sysroot /path/to/arm64-resolute-sysroot --work-dir ~/mesa-work --out-dir ~/tb378fc-out/debs
python3 tools/build-libcamera-deb.py --source-dir /path/to/libcamera-zhiyu --sysroot /path/to/arm64-resolute-sysroot --work-dir ~/libcam-work --out-dir ~/tb378fc-out/debs

# 组装完整 Ubuntu 26.04 LTS ARM64 桌面 RootFS（自动完成 chroot dpkg/ldd/environment.d 闭环校验）
sudo python3 tools/build-desktop-rootfs.py \
  --iso /path/to/ubuntu-26.04-desktop-arm64.iso \
  --debs-dir ~/tb378fc-out/debs \
  --open-firmware-dir /path/to/linux-firmware \
  --work-dir ~/rootfs-work \
  --output-dir ~/tb378fc-out/desktop-rootfs
```

### 3.4 离线检查、单元测试与 QEMU 启动冒烟

```bash
python3 tools/check-source.py
python3 -m unittest discover -s tests -v
sudo python3 tools/validation/test_switch.py
python3 tools/qemu-smoke-test.py \
  --image ~/tb378fc-out/kernel/arch/arm64/boot/Image \
  --initramfs ~/tb378fc-out/initramfs.cpio.gz \
  --busybox /path/to/busybox-arm64 \
  --log-dir ~/tb378fc-out/qemu-smoke
```
