# 双系统、真机验证与恢复（Android Header v4）

## 安全红线

1. **先备齐本机原厂镜像与固件**：任何刷写操作前，提前在主机本地备份当前活动槽位的原厂 `boot.img`、`init_boot.img`、`vendor_boot.img`、`dtbo.img`、`vbmeta.img` 以及本机提取的闭源固件（`/vendor/firmware`、`/modem/image`、`BTFM.bin`），**严禁提交到 Git**。
2. **直接使用 Android Header v4（不要用 `fastboot boot`）**：**联想 TB378FC 原厂 ABL 不支持 `fastboot boot`**，Header v2 临时启动镜像无法生效。全部开发测试与日常启动一律通过 `tools/build-boot-triplet.py` 生成 **Header v4** 分区镜像，在备份原厂镜像后刷入目标槽位测试。
3. **理解 GKI v4 分区职责**：SM8750 GKI v4 中，**内核 `Image` 与 `cmdline` 在 `boot`，initramfs 与 DTB 在 `vendor_boot`，空通用 ramdisk 占位在 `init_boot`**。
   - **首次测试或修改了 DTB / initramfs**：必须刷入完整三件套（`boot` + `init_boot` + `vendor_boot`）；
   - **后续仅修改内核 `Image` 或启动参数**（DTB 与 initramfs 未变）：重新打包后只需重刷 `boot_<slot>` 分区即可快速迭代验证。
4. **切槽测试须先同步 A/B 底层固件**：若打算将 Linux 刷入闲置槽位（例如 Android 在 A 槽、Linux 刷入 B 槽），请务必先确认 B 槽的 `xbl`/`abl`/`tz`/`hyp`/`dsp`/`modem` 等底层固件与 A 槽同步，否则切槽会直接陷入 EDL。如不想碰 B 槽与 `vbmeta`，请采用默认的**同 A 槽三件套轮换**路线。

---

## 第一步：准备本机 ABL DTB 与签名私钥

1. 在本地使用本机提取的原厂 `vendor_boot.img` / `dtbo.img`，通过 `third_party/oneplus/tools/build-erhai-abl-symbols.py` 为编译产出的 `sm8750-lenovo-tb378fc.dtb` 注入本机专属的 contained ABL overlay sink（切勿使用一加默认 base index 或他人设备的符号表）。
2. 在本地仓库外生成自己的 RSA-4096 AVB 签名私钥（切勿提交到 Git）：
   ```bash
   mkdir -p private
   openssl genrsa -out private/avb-rsa4096.pem 4096
   ```

---

## 第二步：生成并刷入 Android Header v4 启动镜像

### 路线 A（推荐默认）：同 A 槽三件套（保留原厂 `vbmeta` 与 B 槽）

使用 `--stock` 读取本机原厂三件套的分区大小与 rollback index，补齐 AVB footer，不修改 `vbmeta`：

```bash
# 初次排障可追加 --debug 开启 tb378fc.debug=1 USB 诊断通道；正式使用去掉 --debug
python3 tools/build-boot-triplet.py \
  --kernel out/kernel-tb378fc/arch/arm64/boot/Image \
  --dtb out/sm8750-lenovo-tb378fc-abl.dtb \
  --initramfs out/initramfs.cpio.gz \
  --stock /path/to/private/stock-triplet \
  --key /path/to/private/avb-rsa4096.pem \
  --mkbootimg /path/to/mkbootimg.py \
  --avbtool /path/to/avbtool.py \
  --root UUID=<你的ubuntu根分区UUID> \
  --debug \
  --output out/v4-debug
```

平板进入 Fastboot 后（假设当前活动槽位为 `a`，可用 `fastboot getvar current-slot` 确认），首次刷入完整三件套并重启：

```bash
fastboot flash boot_a out/v4-debug/signed/boot.img
fastboot flash init_boot_a out/v4-debug/signed/init_boot.img
fastboot flash vendor_boot_a out/v4-debug/signed/vendor_boot.img
fastboot reboot
```

> 后续迭代如果**只改了内核 `Image` 或 `cmdline`**（未改动 DTB 与 initramfs），只需要执行：
> ```bash
> fastboot flash boot_a out/v4-debug/signed/boot.img
> fastboot reboot
> ```

### 路线 B（可选）：独立四件套（含 `vbmeta.img --flags 3`，适合 A/B 双槽或快速测试）

如果希望用固定分区大小（96 MiB / 8 MiB / 96 MiB）一并生成带 `--flags 3` 的 `vbmeta.img`（例如刷入已同步底层固件的闲置 `_b` 槽测试）：

```bash
python3 tools/build-boot-triplet.py \
  --kernel out/kernel-tb378fc/arch/arm64/boot/Image \
  --dtb out/sm8750-lenovo-tb378fc-abl.dtb \
  --initramfs out/initramfs.cpio.gz \
  --key /path/to/private/avb-rsa4096.pem \
  --mkbootimg /path/to/mkbootimg.py \
  --avbtool /path/to/avbtool.py \
  --with-vbmeta \
  --root UUID=<你的ubuntu根分区UUID> \
  --debug \
  --output out/v4-slot-b
```

确认 B 槽底层固件完整后刷入并切槽：

```bash
fastboot flash boot_b out/v4-slot-b/signed/boot.img
fastboot flash init_boot_b out/v4-slot-b/signed/init_boot.img
fastboot flash vendor_boot_b out/v4-slot-b/signed/vendor_boot.img
fastboot flash vbmeta_b out/v4-slot-b/signed/vbmeta.img
fastboot --set-active=b
fastboot reboot
```

---

## 第三步：展开 Ubuntu 26.04 根系统、注入本机固件与真机验收

1. 划出独立 `linux_rootfs`（ext4，建议 60–100 GiB），展开由 `tools/build-desktop-rootfs.py` 产出的 `ubuntu-26.04-tb378fc-arm64-rootfs.squashfs`，或直接在现有 Ubuntu 26.04 根系统安装全部 8 个 `tb378fc-*.deb`。
2. 注入从本机提取的联想签名闭源固件（零售版 TrustZone 仅认证联想本机签名的 `vpu35_4v.mbn`、`gen80000_zap.mbn`、`adsp.mdt`）：
   ```bash
   sudo tb378fc-install-firmware --source-dir /path/to/extracted-stock-fw --rootfs /mnt/linux_rootfs
   ```
   > 蓝牙固件同时支持上游 `qca/gngbtfw*`/`gngbtnv*` 与联想原厂 `BTFM.bin` 提取的 `qca/brhbtfw*`/`brhbtnv*`（内核 `0010` 补丁自动回退）。
3. 真机验证要点：
   - 正常亮屏进入桌面，`uname -r` 显示 `7.4.0-rc2-tb378fc-...` 且 `/proc/sys/kernel/tainted` 为 `0`；
   - `dmesg | grep abl-root-identity` 确认已恢复 `lenovo,tb378fc`；
   - 显示设置可原生无缝切换 **60 / 90 / 120 / 144 Hz**；
   - `vulkaninfo --summary` / `glxinfo -B` 确认 Mesa 26.2.6（Adreno 830 / Turnip）生效；`vainfo` 确认 Iris VPU35 硬解就绪；
   - `cam -l` / GNOME Snapshot 验证前后摄与后摄 DW9807 自动对焦正常，且休眠唤醒时镜头无弹响；
   - UFS、Wi-Fi 7、蓝牙、四扬声器（2ch/4ch chmap）、麦克风、后置闪光灯（`tb378fc-torch`）、旋转与 `s2idle` 休眠功耗正常。
4. 全部验证稳定后，重新运行 `tools/build-boot-triplet.py`（**不加 `--debug`**）产出生产版干净 v4 三件套，并参照 [`switch/README.md`](../switch/README.md) 使用 `tb378fc-switch` 在活动 A 槽安全轮换 Ubuntu / Android。

---

## 紧急恢复原厂 Android

如果任何时候无法进入系统，长按电源键+音量下进入 Fastboot，刷回提前备份的本机原厂三件套即可完整恢复 Android（不丢数据）：

```bash
fastboot flash boot_a /path/to/private/stock/boot.img
fastboot flash init_boot_a /path/to/private/stock/init_boot.img
fastboot flash vendor_boot_a /path/to/private/stock/vendor_boot.img
# 若曾修改过 vbmeta，同步刷回原厂 vbmeta.img：
# fastboot flash vbmeta_a /path/to/private/stock/vbmeta.img
fastboot --set-active=a
fastboot reboot
```
