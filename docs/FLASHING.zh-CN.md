# 一键刷机、同槽双系统与紧急恢复指南（TB378FC）

本文面向已解锁 Bootloader 的 **联想小新 Pad Pro GT 13（TB378FC，8GB+256GB，SM8750P）**。

## 关键安全红线（必读）

1. **原厂 ABL 仅支持 Android Header v4 分区启动**：`fastboot boot xxx.img`（Header v2）在 TB378FC 上无效，切勿使用。
2. **严禁对原厂 `vbmeta` 使用 `--disable-verity --disable-verification`（`Flags=3`）**：联想 ZUXOS 原厂 ABL 会拒绝启动 `Flags=3` 的 Android 槽位（重试 7 次后置 `slot-unbootable: yes`）。同槽双系统**全程保留未修改的原厂 `vbmeta`（`Flags=0`）**。
3. **严禁在 SM8750 上运行 `qbootctl -s` 切槽**：`qbootctl` 会错误清空 GPT 分区属性导致重启掉入 EDL (9008)。本项目的双系统采用**同活动槽位（`_a` 或 `_b` 自动识别）启动三件套安全轮换**，永不跨槽改写 GPT 启动位。
4. **先备份后刷机**：一键向导会在任何写入前自动将本机 `boot` / `init_boot` / `vendor_boot` / `dtbo` / `vbmeta` 与主 GPT 表头备份至本地 `backups/<序列号>/` 并做只读保护。

---

## 方式一（推荐）：使用 Release 标准一键刷机包

### 1. 环境与设备准备
1. **平板端**：运行原厂 **ZUXOS**（无论当前处于 `_a` 槽还是 `_b` 槽均可，OTA 升级后的 `_b` 槽同样原生支持）：
   - 开启「设置 → 开发者选项 → USB 调试」并连接电脑授权 ADB；
   - 安装 **KernelSU**（LKM 模式刷入对应 `init_boot`），打开 KernelSU →「超级用户」→ 为 **`Shell (com.android.shell)`** 开启 root 权限。
2. **电脑端**（支持 Windows 10/11、Linux、macOS）：
   - 安装 **Python 3.10+**；
   - 安装 Android SDK `platform-tools`（确保终端可执行 `adb` 与 `fastboot`，或直接将 `platform-tools` 文件夹放在刷机包目录内）。
3. **下载刷机包**：从 GitHub Releases 下载以下文件：
   - `tb378fc-ubuntu-v0.6.5-flash-kit.zip`（解压到任意英文路径目录）
   - `ubuntu-26.04-tb378fc-v0.6.5-arm64-rootfs.squashfs.part00`
   - `ubuntu-26.04-tb378fc-v0.6.5-arm64-rootfs.squashfs.part01`
   - 将两个 `.part00` / `.part01` 文件放入解压后的刷机包根目录或 `rootfs/` 子目录中。

### 2. 启动一键向导
- **Windows**：双击 `flash-windows.bat`
- **Linux / macOS**：在终端执行 `./flash-linux.sh`

向导提供 5 个交互选项（也可通过 `python3 install.py --mode <模式>` 命令行调用）：

#### 场景 A：首次全新双系统装机（选择 `[1]`）
- **阶段 1（自动无损切分 GPT 分区）**：
  1. 自动读取平板型号（严格校验 `TB378FC`）、序列号、SoC 序列号与当前活动槽位（`_a` 或 `_b`）；
  2. 自动备份原厂启动五件套与 GPT 表头至 `backups/<序列号>/stock_<槽位>/`，并校验本机 `dtbo` 符号 100% 覆盖；
  3. 经你输入 `YES` 确认后，本地自动生成本机 RSA-4096 私钥签名临时 RAM 分区镜像（`v4-setup`），重启进入内存环境将原厂 `userdata` 缩容并新建 `ubuntu` 分区（默认 `120 GiB`，可通过 `--ubuntu-gib` 调整）；
  4. 分区完成后平板自动退回 Fastboot，脚本校验 `partition-size:ubuntu` 无误后自动恢复原厂 Android 启动三件套并执行 `fastboot -w` 初始化新 `userdata`（**注意：首次缩容必须格式化 Android 数据，请提前备份个人文件**）。
- **阶段 2（写入 Ubuntu 26.04 与激活双系统）**：
  1. 平板重启进入全新 ZUXOS 后，重新开启 USB 调试并配好 KernelSU Shell root；
  2. **再次双击运行一键刷机脚本**，向导检测到 `ubuntu` 分区已存在后自动进入阶段 2（同下方场景 B）。

#### 场景 B：已有 `ubuntu` 分区一键重装系统（选择 `[2]`）
- 不改动 GPT 分区表、不清空 Android 数据：
  1. 自动备份当前 Android（含 KernelSU）三件套至 `backups/<序列号>/android-ksu_<槽位>/`；
  2. 将分卷 RootFS 推送至平板并校验完整 SquashFS SHA-256，使用 8 核 Oryon 并行解压纯净 Ubuntu 26.04 (`v0.6.5`) 至 `ubuntu` 分区；
  3. 自动从本机正在运行的 Android `/vendor`、`/vendor/firmware_mnt`、`/mnt/vendor/persist` 提取联想签名闭源固件（`adsp`、`cdsp`、`vpu35_4v.mbn`、`gen80000_zap.mbn`、自动切片 `aw882xx_acf.bin`、触控与 Wi-Fi/BT 固件、ADSP 传感器校准配置）注入 Ubuntu；
  4. 使用本机原厂 rollback index 本地签名生产版 `v4-ubuntu` 启动三件套，写入 `/.tb378fc-root`、`/etc/fstab`、`/etc/tb378fc-switch.conf` 与 `/switch/{android,ubuntu}` 仓库；
  5. 自动安装并启用 KernelSU 模块 `tb378fc_ubuntu_switch`。首次进入 Ubuntu 将显示官方 GNOME 初始向导创建你的个人账户。

#### 场景 C：不格盘原地升级（选择 `[3]`）
- 保留 Ubuntu 分区全部用户账户与个人文件，自动挂载现有 `ubuntu` 分区升级全部 `debs/*.deb`、更新内核启动三件套及双系统切换配置。

---

## 日常双系统一键互切

| 切换方向 | 操作方法 |
|---|---|
| **Android → Ubuntu** | 打开 **KernelSU** App → 底部「模块」→ 找到 **TB378FC Ubuntu Switch** → 点击 **「操作 (Action)」** 按钮，校验三件套 SHA-256 后自动重启进入 Ubuntu |
| **Ubuntu → Android** | 打开 Ubuntu 应用菜单 → 点击 **「切换到 Android」** 图标（或终端执行 `sudo tb378fc-switch to-android`），输入密码后自动校验写回 Android 三件套并重启进入 ZUXOS |

---

## 方式二（开发者进阶）：从源码生成通用刷机包

在 WSL2 / Ubuntu x86_64 原生 `ext4` 环境下完成内核、initramfs、`.deb` 与纯净桌面 SquashFS 构建后（详见 [构建指南](BUILD.zh-CN.md)），可通过 `tools/build-boot-triplet.py` 单独构建 Android Header v4 镜像，或用 `tools/build-flash-bundle.py` 一条命令生成完整通用刷机包与 GitHub Release 分卷资产：

```bash
python3 tools/build-flash-bundle.py \
  --version 0.6.5 \
  --kernel out/kernel-tb378fc/arch/arm64/boot/Image \
  --dtb out/sm8750-lenovo-tb378fc-abl.dtb \
  --prod-initramfs out/initramfs-tb378fc-v7.4-0.6.5.cpio.gz \
  --setup-initramfs out/initramfs-tb378fc-setup-0.6.5.cpio.gz \
  --mkbootimg /path/to/mkbootimg.py \
  --unsquashfs-arm64 /path/to/unsquashfs-arm64 \
  --busybox-arm64 /path/to/busybox-arm64 \
  --debs-dir out/debs-release-0.6.5 \
  --rootfs-squashfs out/desktop-rootfs-0.6.5/ubuntu-26.04-tb378fc-arm64-rootfs.squashfs \
  --split-mib 1900 \
  --output-dir out/flash-bundle-v0.6.5
```

打包器会自动执行隐私与闭源固件红线扫描，确保产物不含任何 `.pem` 私钥、序列号或厂商受限固件。

---

## Fastboot 紧急恢复原厂 Android

若任何时候无法正常进入系统：
1. 长按平板 **电源键 + 音量下** 进入 Fastboot 模式并连接电脑；
2. 双击 `flash-windows.bat`（或运行 `./flash-linux.sh`）选择 **`[5] Fastboot 紧急恢复本机备份的原厂 Android 启动分区`**；
3. 或手动刷回 `backups/<序列号>/stock_<槽位>/` 下的三件套（切勿附加 `--disable-verity`）：
   ```bash
   fastboot flash boot_b backups/<序列号>/stock_b/boot.img
   fastboot flash init_boot_b backups/<序列号>/stock_b/init_boot.img
   fastboot flash vendor_boot_b backups/<序列号>/stock_b/vendor_boot.img
   fastboot reboot
   ```
