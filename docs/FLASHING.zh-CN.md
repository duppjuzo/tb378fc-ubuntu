# 双系统、真机验证与恢复

## 安全红线

1. **先备原厂镜像**：任何操作前，提前在本机外部保存当前槽位原厂 `boot.img`、`init_boot.img`、`vendor_boot.img`、`dtbo.img` 以及本机提取的闭源固件，**严禁提交到 Git**。
2. **永远先用 `fastboot boot` 临时 RAM 验证**：新内核、DTB 或 initramfs 必须先通过 `tools/build-boot-v2.py` 打包为 Header v2 镜像并执行临时启动，确认屏幕、触摸、存储、网络与核心外设稳定后，再封装并写入 GKI v4 持久三件套。
3. **不触碰未知分区表**：不要套用其他设备的分区表或他人的 AVB 签名镜像。

---

## 第一步：临时 RAM 验证（推荐，零分区写入）

1. 在本地使用本机提取的原厂 `vendor_boot` / `dtbo`，通过 `third_party/oneplus/tools/build-erhai-abl-symbols.py` 为编译产出的 `sm8750-lenovo-tb378fc.dtb` 添加 contained ABL overlay sink（切勿使用一加默认 base index）。
2. 生成 `boot-v2.img`（初次排障可加 `--debug` 启用 `tb378fc.debug=1`）：
   ```bash
   python3 tools/build-boot-v2.py \
     --kernel out/kernel-tb378fc/arch/arm64/boot/Image \
     --dtb out/sm8750-lenovo-tb378fc-abl.dtb \
     --initramfs out/initramfs.cpio.gz \
     --mkbootimg /path/to/mkbootimg.py \
     --root UUID=<你的ubuntu根分区UUID> \
     --debug \
     --output out/boot-v2
   ```
3. 平板进入 Fastboot 模式（`adb reboot bootloader` 或按键组合），在 Windows/Linux 主机执行：
   ```bash
   fastboot boot boot-v2.img
   ```
4. 验证要点：
   - 正常亮屏进入桌面，`uname -r` 显示 `7.4.0-rc2-tb378fc-...` 且 `/proc/sys/kernel/tainted` 为 `0`；
   - `dmesg | grep abl-root-identity` 确认已恢复 `lenovo,tb378fc`；
   - 显示设置可原生无缝切换 **60 / 120 / 144Hz**；
   - UFS、Wi-Fi 7、蓝牙、四扬声器、麦克风、前后摄、后置闪光灯（`tb378fc-torch`）、旋转与 `s2idle` 休眠正常；
   - 若系统内仍是旧版未打补丁的 Mesa 出现多线纹理异常，可临时在用户级 override 中设置 `GALLIUM_THREAD=false`，升级到 `packaging/mesa/` 26.2.6 补丁版 Mesa 后即可移除。

---

## 第二步：封装并安装 GKI v4 三件套（确认临时启动稳定后）

1. 在平板 Ubuntu 内安装本次产出的 `.deb`（确保 `/lib/modules/<new-release>` 就位）：
   ```bash
   sudo dpkg -i tb378fc-kernel-image_*.deb tb378fc-device-support_*.deb tb378fc-audio_*.deb tb378fc-device_*.deb
   ```
2. 在主机端生成并签名本机 AVB GKI v4 三件套（不加 `--debug`，保持生产环境默认关闭 USB 调试）：
   ```bash
   python3 tools/build-boot-triplet.py \
     --kernel out/kernel-tb378fc/arch/arm64/boot/Image \
     --dtb out/sm8750-lenovo-tb378fc-abl.dtb \
     --initramfs out/initramfs.cpio.gz \
     --stock /path/to/private/stock-triplet \
     --key /path/to/private/avb.pem \
     --mkbootimg /path/to/mkbootimg.py \
     --avbtool /path/to/avbtool.py \
     --root UUID=<你的ubuntu根分区UUID> \
     --output out/triplet-v7.4
   ```
3. 参照 [`switch/README.md`](../switch/README.md) 使用 `tb378fc-switch` 在活动 A 槽安全轮换 Ubuntu / Android 启动三件套。

---

## 紧急恢复原厂 Android

如果任何时候无法进入系统，长按电源键+音量下进入 Fastboot，刷回提前备份的本机原厂三件套即可完整恢复 Android（不丢数据）：

```bash
fastboot flash boot_a /path/to/private/stock/boot.img
fastboot flash init_boot_a /path/to/private/stock/init_boot.img
fastboot flash vendor_boot_a /path/to/private/stock/vendor_boot.img
fastboot reboot
```
