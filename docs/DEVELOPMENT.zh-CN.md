# 开发与维护规范

## 强约束与红线

1. **绝不混入一加/小米专属板级驱动**：禁止启用或移植 `SC8547A` / SUPERVOOC / `CHARGER_ERHAI_PPS` / `CHARGER_CPS8601` / `CHARGER_XIAOMI_MCA` / `KEYBOARD_ONEPLUS_POGO` / `SC820CS` / `SC1300CS` / `CN3927` / `KTZ8866` / `AW88261` / `DRM_PANEL_NOVATEK_NT36536` / `HID_XIAOMI`。`tools/build-kernel.sh` 与 CI 会自动断言这些配置均为关闭状态。
2. **隐私与版权红线**：严禁向 Git 提交闭源固件、本机校准数据、AVB 私钥、原厂镜像备份、用户密码或设备序列号。
3. **无硬编码用户**：`packaging/device-support/tree/` 中禁止出现 `pad`、`/run/user/1000`、`7.2.0-tb378fc-wifi+`、`GALLIUM_THREAD=false` 等硬编码，`tools/check-source.py` 会在提交和 CI 阶段拦截回归。
4. **开发与编译环境**：Linux 内核源码与构建输出必须位于原生大小写敏感 ext4 文件系统，禁止在 Windows NTFS (`/mnt/c/...`) 下解包或编译内核。

## 目录约定

- `kernel/patches/0001..0009-*.patch`：按子系统拆分的原子补丁，直接 `git am` 到 `kernel/source.json` 固定的上游提交。
- `kernel/configs/tb378fc.config`：仅包含 TB378FC 差异与隔离约束，与上游 `sm8750-*.config` 组合合并。
- `packaging/device-support/`：最终安装的文本配置、动态会话助手脚本与相对路径 `symlinks.json`，由 `tools/build-debs.py` 打包。
- `packaging/mesa/`：同步自上游 `onepluspad-2pro-mainline` 的通用 Adreno 830v1 Mesa 26.2.6 补丁。
- 历史阶段脚本与旧外置模块快照已从当前分支移除，需要溯源时可直接查阅 Git 历史（`main` 分支）。

## 本地验证命令

```bash
python3 tools/check-source.py
python3 -m unittest discover -s tests -v
python3 tools/validation/test_switch.py
shellcheck initramfs/init initramfs/shutdown initramfs/usb-diag tools/build-kernel.sh tools/build-initramfs.sh
```
