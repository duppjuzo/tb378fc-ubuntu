# Ubuntu / Android 同活动槽切换器（`_a` / `_b` 自适应）

由一加项目 `erhai-switch`（提交 `adc57231281ac5bc8eb8ee012ffb04663e9f8226`）适配至联想 TB378FC，遵循 GPL-2.0-only 许可并执行整分区 SHA-256 读回校验。

- **同槽安全轮换**：Android 与 Ubuntu 共享当前活动槽位（通过 `TB378FC_SLOT=_a` 或 `_b` 配置绑定，永不调用在 SM8750 上会损坏 GPT 属性的 `qbootctl -s`，也不修改原厂 `vbmeta`）。
- **双端共用归档**：两套系统的 `boot.img` / `init_boot.img` / `vendor_boot.img` 与 `SHA256SUMS` 存放在 `PARTLABEL=ubuntu` 根分区的 `/switch/android/` 与 `/switch/ubuntu/` 下，根分区包含 `/.tb378fc-root`（`tb378fc-root79`）标记。
- **设备唯一身份校验**：Ubuntu 侧读取 `/etc/tb378fc-switch.conf`，Android KernelSU 模块读取本地 `device.conf`，严格核对本机 `TB378FC_SERIAL`、`TB378FC_SOC_SERIAL` 与 `TB378FC_SLOT`，防止跨机误刷其他平板的启动镜像。

日常安装无需手动打包：运行根目录或刷机包中的一键向导（`tools/flash-tb378fc.py` / `install.py`）会自动读取本机序列号与活动槽位，完成签名、写入 `/switch/` 并安装激活 KernelSU 模块。详见 [一键刷机与双系统说明](../docs/FLASHING.zh-CN.md)。
