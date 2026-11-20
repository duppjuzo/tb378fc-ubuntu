# 硬件支持状态（Linux 7.4 统一架构）

目标设备：**联想小新 Pad Pro GT 13（TB378FC / Qualcomm SM8750P / 8GB + 256GB / 已解锁 Bootloader）**。

## 硬件模块与 Linux 7.4 适配清单

| 硬件模块 | 驱动与设备树实现 | 7.4 统一架构变更与状态 |
|---|---|---|
| 显示面板 | `panel-novatek-nt36526` + `tb378fc-panel-bias` + 双路 `tb378fc-aw99706b` | 适配 7.4 `MIPI_DSI_MODE_DSC_ALL_SLICES_IN_PKT`；3504×2190 双 DSI DSC |
| 60/120/144Hz 刷新率 | `panel-novatek-nt36526` + `drm/msm` 原生无缝 DFPS | 彻底淘汰外置 `refresh-live76.ko` 结构体勾子，合入主线内核态双缓冲切频 |
| 触控与手写笔 | `nt36536` (`lenovo,tb378fc-nvt`，上升沿 IRQ，Trim ID `0x15/0x23/0x65/0x03`) | 继承上游深睡防卡死修复；保留 TB378FC 原生压感上限 4095 与严格 ID 校验 |
| GPU (Adreno 830v1) | `drm/msm` 7.4 + Mesa 26.2.6 (`packaging/mesa/`) | 继承 7.4 GMU/shrinker/hw fence/LLCC 32 切片修复；Mesa 同步 `freedreno-thread-safe-tex-seqno.patch`，移除 `GALLIUM_THREAD=false` |
| 内部存储 (UFS) | `ufs-qcom` + `phy-qcom-qmp-ufs` + `ext4`（全部内置 `=y`） | 废除早期 DT overlay 与外置 `.ko`，直出设备树 + 继承 7.4 MCQ 与防死锁修复 |
| Wi-Fi 7 & 蓝牙 | WCN7860 (`ath12k` + `hci_uart` QCA / `uart14`) | 内置编译，保留 `qcom_geni_serial` 负别名修复与动态蓝牙地址服务 |
| 音频（四扬声器 + 麦） | 四路 `aw88480` (GPIO-I2C) + `wcd939x` SoundWire + AudioReach Q6APM | 合入标准 `/lib/modules`；继承 7.4 ADSP 首包防丢与 GLINK 死锁修复；保留 WCD939x IRQ 生命周期与联想原生 ADC 映射 |
| 相机（前 8MP / 后 13MP） | `gc08a8` (CSIPHY1) + `ov13b10` (CSIPHY5) + `qcom-camss` + `phy-qcom-mipi-csi2` | 迁移至 Linux 7.4 独立 CSI-2 PHY 图谱；保留 OV13B10 联想 VAF 上电时序与 CAMSS Gen4 256B `sizeimage` 对齐 |
| 后置闪光灯 / 手电筒 | `leds-qcom-flash` (`pm8550_flash` `led-0`, 通道 1+4, 默认关闭) | 内置设备树，淘汰 `live-flash-overlay70.ko`；`tb378fc-torch.socket` 改为 `video` 组 + 动态 seat0 鉴权 |
| 视频编解码 (Iris) | `qcom-iris` (`vpu35_4v.mbn`) | 继承上游 7.4 Iris VPU 修复 |
| USB-C & DP Alt Mode | `dwc3-qcom` + `ucsi_glink` + `wcd939x-usbss` + `mdss_dp0` (HBR3) | 继承上游 7.4 DP AUX 重试与稳定性修复 |
| 电池与充电 | `qcom_battmgr` (`lenovo,tb378fc-pmic-glink`) | 保留电流符号反转、0..900mA 有界 USB ICL 写入与只读 `0x60` 诊断；绝不混入一加/小米快充驱动 |
| 温控与诊断 | SM8750P 118°C CPU 被动降频 + 8GB 安全 `ramoops@c2000000` (4 MiB) + Gunyah 看门狗 | 首次补齐 Oryon CPU 被动热节流；显式关闭 `CONFIG_CRYPTO_DEV_QCE` 保障 `s2idle` CX 断电 |
| 平台安全与容器 | `CONFIG_SECURITY_APPARMOR=y`、`CONFIG_NF_TABLES=m`、Rust Binder (Waydroid)、Gunyah VM | 同步上游 5 个公共 Kconfig 片段，Snap 恢复完整 AppArmor 沙箱 |

## 已知边界与注意事项

- **真机回归待测**：本次重构已在 WSL2 ext4 环境完成完整 ARM64 交叉编译、`dtc` 设备树校验、Kconfig 隔离断言与离线单测，**尚未在实体 TB378FC 平板上完成真机回归**，请务必先通过 `fastboot boot boot-v2.img` 验证。
- **固件需自行提取**：仓库不含任何闭源固件（`adsp`、`gen80000_zap`、`vpu35_4v`、`novatek/tb378fc.bin`、`aw88480-native-music.acf`、WCN7860 固件等），详见 [`firmware/`](../firmware/)。
- **未适配项**：光照传感器、原厂磁吸键盘/笔充（注意：一加/小米相关驱动严禁混入）暂不计为已支持。
