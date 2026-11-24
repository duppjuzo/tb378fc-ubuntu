# 真机验证摘要（更新至 v0.6.6）

验证设备：联想小新 Pad Pro GT 13（`TB378FC`，8GB/256GB，Qualcomm SM8750P）。所有设备序列号、MAC 地址、密钥及个人路径均已脱敏。

## 1. v0.6.6（Linux 7.4.0-rc2 `0001..0015` + 桌面体验与磁吸键盘内核支持）验证（2026-11）

1. **冷启动触控、GNOME 50 多窗口/Chrome 虚拟键盘与 Oryon 4.32 GHz 睿频（真机已验证）**：
   - 刷入合入补丁 `0014` 与精简早期 initramfs 的 GKI v4 启动三件套后执行冷重启：`dmesg` 中 `nt36536` 报错数为 `0`，`IRQ 207/209` 进桌面立即就绪，彻底消除开机需息屏重亮才能恢复触控的问题；同时消除了早期 Adreno 830 GMU OOB 5 秒超时。
   - 在 GNOME 50 Wayland 会话同时打开多个应用窗口及 Google Chrome 地址栏/网页输入框进行触屏点击测试：虚拟键盘在客户端窗口（`global.stage`）与 Chrome（Wayland Text Input v3）中均稳定自动弹出与收起，蓝牙外设共存逻辑正常。
   - `schedutil` + `boost=1` 下实机单核负载观测到 CPU6/CPU7 升频至 **4,320,000 kHz（4.32 GHz）**；`a600000.usb-mux` 注册正常，`109ab000.tn` 已禁用，`systemctl --failed` 为 `0` 失败单元。
   - 常驻内存精简与 `zram` 二级重压缩验证（保留 `snapd` 与应用中心）：全系统常驻 Python 守护进程由 7 个收敛至 **2 个**（系统级 `tb378fc-thermal-guard` + 会话级 `tb378fc-v4l2-camera-bridge`），`fwupd` 切换为 `--timed-exit` 按需唤醒退出，桌面用户态总 `PSS` 实测由 `1979.0 MiB` 降至 **`1812.9 MiB`（净省 `166.1 MiB`）**，`MemAvailable` 升至 `7695 MiB`。
2. **联想 TB378FC 磁吸触点键盘驱动（补丁 `0015`）分级验证记录**：
   - **离线验证（已通过）**：64 字节旋转 XOR 加扰/解扰、PPP 帧编解码、按键/四指触控板协议单测、`checkpatch.pl`（0 错误/0 警告）、`dt-doc-validate`（0 错误）与 `-Werror` 编译全部通过。
   - **平板端实机探测验证（未吸附键盘状态，已通过）**：`a88000.serial: ttyHS1`（`IRQ 190`）、`lenovo-pogo-keyboard serial1-0` 绑定成功，未吸附时自动断电（`connected=0, plug_status=0`），`IRQ 210 (lenovo-kb-plug)`、`IRQ 211 (lenovo-kb-wake)` 与 `power/wakeup=enabled` 全部正常。
   - **待实机验证项**：物理磁吸键盘吸附握手、矩阵按键、四指触控板手势与指示灯同步待接入实体键盘后实测确认。

## 2. v0.6.5（Linux 7.4.0-rc2 + ZUXOS 2.0.10.320 同槽双系统）真机验证（2026-11）

1. **Slot `_b` / `_a` 同活动槽位双系统闭环**：
   - 在 OTA 升级后的 `ZUXOS 2.0.10.320`（活动槽位 `_b`，保留未修改原厂 `vbmeta_b` `Flags=0`）上验证 KernelSU LKM (`6.6.102-android15-8`) + `tb378fc_ubuntu_switch` 模块与 Ubuntu 桌面「切换到 Android」双向往返切换；
   - 切换全程保持 `Active=1, Successful=1, Bootable=1`，`2331/2331` ABL 符号与 `91/91` DTBO 条目 100% 覆盖匹配，120 GiB `ubuntu` (`sda19`) 根分区数据与设置跨系统完整保留。
2. **开机亮度持久化与传感器空闲唤醒验证**：
   - 预设桌面背光为 `1460/4095`（约 35%）后执行真机冷重启，`systemd-backlight` 准确保存并恢复 `1460`；GDM 50 登录界面与桌面会话在 `T+0s`、`T+4s`、`T+60s` 恒定维持 `1460`，彻底消除开机跳 100% 亮度的棘轮效应。
   - 实机 `strace` 采样空闲态 `systemd-logind` 4 秒：**0 次子进程 fork / 0 次 sysfs 背光写入**，同时 `net.hadess.SensorProxy` 实时光照读数（`~88.9 lux`）与四向旋转、皮套霍尔合盖 `s2idle` 休眠均正常。
3. **显示、多媒体与外设回归**：
   - VESA DSC 1.2 RC 参数修正后 3504×2190 @ 60/90/120/144 Hz 切频正常；
   - Adreno 830 Mesa 26.2.6 Vulkan/OpenGL、Iris VPU35 H.264/HEVC/VP9/AV1 VA-API 硬解、四扬声器四向随屏旋转、双麦录音、USB-C DP 视频+音频输出、前后摄横屏方向与后摄 DW9807 自动对焦均在实机通过验证。

## 3. 离线构建与自动化护栏验证

- `tools/check-source.py`：全仓文本语法、相对符号链接、禁止硬编码与私钥扫描通过；
- `shellcheck`：全部 initramfs、构建器与设备支持脚本无错误通过；
- `pytest`：43 项单元测试（含纯 Python AVB 2.0 `SHA256_RSA4096` 签名与 `avbtool verify_image` 互验、DTBO 符号覆盖校验、4K 扇区 GPT 无损切分与 CRC32 校验、AW88480 Music ACF 切片及同槽切换逻辑）全部通过；
- `checkpatch.pl` & `dtb_audit` (`9/9`)：15 个内核补丁与 TB378FC 设备树保留内存/温控/板级隔离审计全部通过。
