# 硬件支持状态（v0.6.6 / Linux 7.4 统一架构）

目标设备：**联想小新 Pad Pro GT 13（TB378FC / `zhiyu` / Qualcomm SM8750P / 8GB + 256GB / 已解锁 Bootloader）**。

## 硬件模块与真机验证状态

| 硬件模块 | 驱动、补丁与用户态组件 | 真机实测状态（v0.6.6） |
|---|---|---|
| 显示面板与 DSC | `panel-novatek-nt36526` (`0001`) + `tb378fc-panel-bias` (`0002`) + VESA DSC 1.2 RC 参数编程 (`0011`) | **正常**：3504×2190 分辨率清晰无彩边，支持 DPMS 息屏唤醒 |
| 60/90/120/144Hz 刷新率 | `panel-novatek-nt36526` + `drm/msm` 双缓冲无缝 DFPS (`0003`) | **正常**：四档刷新率均对齐 ~183 MHz 压缩 DSI 链路时钟，桌面切换无黑屏 |
| 屏幕背光与亮度记忆 | 双路 `tb378fc-aw99706b` (`0002`) + `tb378fc-device-support 0.6.6` | **正常**：`40..4095` 精细调光；已修复 GNOME 50 自动亮度棘轮效应，冷重启与双系统互切后持久保持设定亮度 |
| 触控、手写笔与屏幕键盘 | `nt36536` (`0005` + `0014` 延迟固件加载) + GNOME 50 Wayland OSK (`keyboard.js` + Text Input v3) | **正常**：冷启动进桌面即刻响应触控（无需息屏重亮），4095 级压感正常，多窗口与 Chrome/Chromium 触屏自动呼出虚拟键盘 |
| 原厂磁吸触点键盘与触控板 | `lenovo_pogo_keyboard` + `qcom_geni_serial` 半双工 `tx-en-gpios` (`0015`, `0xa88000` @ `921600`) | **驱动已合入（待实机吸附键盘联调）**：平板端 `ttyHS1`、霍尔/唤醒中断 (`IRQ 210/211`) 与未连接断电状态已实机验证；物理键盘按键/触控板/LED 待实测 |
| GPU (Adreno 830) | `drm/msm` 7.4 + `tb378fc-mesa 26.2.6-0.2.0` + 定制 `mutter 50.1+tb378fc1` | **正常**：Freedreno OpenGL/GLES 与 Turnip Vulkan 1.4 硬件加速正常 |
| 视频硬解 (Iris VPU35) | `qcom-iris` (`0010`) + `tb378fc-iris-vaapi 0.4.0+git.4125b11-4` (`0007`) + `tb378fc-video-defaults 0.6.6` | **正常**：H.264 / HEVC / VP9 / **AV1** VA-API 硬解与 Chromium 兼容正常 |
| 音频（四扬声器 + 双麦） | 四路 `aw88480` + `wcd939x` (`0004`) + `tb378fc-audio 0.6.6` | **正常**：四扬声器随屏幕四向旋转自动重映射左右声道；双路模拟麦克风立体声录音正常 |
| USB-C & DP 音视频输出 | `dwc3-qcom` (`mode-switch` `0014`) + `ucsi_glink` + `mdss_dp0` + DP0 Audio (`0013` + `0014`) | **正常**：USB Host/Gadget、外接 5K 4-lane DP 显示器扩展桌面、热拔插防死机及 DP 数字音频输出正常 |
| 相机（前 8MP / 后 13MP） | `gc08a8` + `ov13b10` + `dw9807-vcm` (`0007`) + 横屏旋转 (`0012`) + `tb378fc-libcamera 0.7.2-0.2.2` + `v4l2loopback` | **正常**：前后摄画面方向正确，后摄 CDAF 自动对焦正常，休眠无镜头弹响，支持 V4L2 虚拟摄像头桥接 |
| 后置闪光灯 / 手电筒 | `leds-qcom-flash` (`pm8550_flash`) + `tb378fc-torch` | **正常**：动态 `seat0` / `video` 组鉴权控制正常 |
| 传感器与磁吸皮套 | `tb378fc-sensors 0.2.3` (`hexagonrpcd` + `sensh` + `tb378fc-sensord`) | **正常**：四向重力自动旋转、磁吸皮套霍尔合盖 `s2idle` 休眠/开盖唤醒、真实物理 `lux` 上报 + GNOME Shell 原生绝对照度 LUT 自适应背光（抗眩光防抖、关灯 `0 lux` 丝滑降到底、四向旋转零漂移） |
| Wi-Fi 7 & 蓝牙 | WCN7860 (`ath12k` + `btqca` `0010`/`0014` + AOSP BQR + WirePlumber AAC/LDAC) | **正常**：Wi-Fi 7 高速连接、蓝牙高清音频与蓝牙键鼠/触屏键盘共存正常 |
| 内部存储与内存管理 | UFS 4.0 MCQ + UFS HWMON + MGLRU + zram (`tb378fc-zram`) | **正常**：8GB 专用安全保留内存布局（`ramoops@c2000000`），低写放大 journald/I/O 调度 |
| 电池、充电、睿频与温控 | `qcom_battmgr` (`0006`/`0014`) + `schedutil` **4.32 GHz** Prime 睿频 (`0014`) + Oryon 118°C 被动降频 + 三档温控 | **正常**：充放电功率/电量显示正常，实机达成 4.32 GHz 单核睿频，支持省电/平衡/性能三档温控与持久化 |
| 同槽双系统互切 | `switch/tb378fc-switch` (`_a` / `_b` 自适应) + KernelSU 模块 | **正常**：ZUXOS (`2.0.10.320` 等) ↔ Ubuntu 26.04 双向一键切换稳定可靠 |

## 已知边界与隔离红线

- **严禁混入非本机板级驱动**：一加/小米专属快充芯片（`SC8547A` / SUPERVOOC）、一加 Pogo 键盘 / `CPS8601` 笔充及 16GB `ramoops@afff00000` 均已显式屏蔽。
- **联想原厂磁吸触点键盘 / 手写笔无线充**：磁吸触点键盘与四指触控板主线驱动（`lenovo_pogo_keyboard`, `0015`）已合入并完成平板端探测验证，物理键盘吸附实测待补齐；手写笔无线充暂未纳入主线支持范围。
- **闭源固件合规**：仓库与 Release 包均不分发联想/高通受限固件；由一键刷机脚本从用户本机 Android 分区现场提取注入。
