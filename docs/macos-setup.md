# macOS 安装与自动同步

本文只描述 macOS 客户端。完整背景和通用步骤见 [README](../README.md) 与
[教程](tutorial.md)。

## 1. 安装

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,ble]'
cp config.example.toml config.toml
```

## 2. 配置数据源

把 `feed_path` 指向本地脱敏 `dashboard.json`，或指向已挂载的只读 NAS 共享。
首次试跑可使用 `./examples/dashboard.example.json`。

客户端只读取 feed 和本机 Codex 数据；GitHub Token、NAS 凭据和原始 API 响应
不应出现在 macOS 配置或日志中。

## 3. 渲染检查

```bash
.venv/bin/epd-dashboard render \
  --feed examples/dashboard.example.json \
  --output rendered \
  --profile 4.2_400_300_three_color
```

打开 `rendered/codex.png` 和 `rendered/github.png`，确认中文字体、数字和
贡献热力图显示正常。

## 4. 发现并授权设备

```bash
.venv/bin/epd-dashboard discover --config config.toml
```

首次运行保持 `slots.configured = false`。确认设备 profile 和槽位清单后，
设置 `device.identifier`、两个目标槽位及 `slots.protected`，再改为
`configured = true`。

macOS 可能要求给终端或 Python 授予蓝牙权限：
系统设置 → 隐私与安全性 → 蓝牙。

## 5. 手动同步

```bash
.venv/bin/epd-dashboard sync-once --config config.toml --only codex --combined
```

`--combined` 会把 Codex 与 GitHub 合成到 Codex 槽位。若你的设备配置了两个
独立槽位，可去掉 `--combined` 并运行完整的 `sync-once`。

## 6. 自动同步

手动同步成功后：

```bash
.venv/bin/epd-dashboard install-autosync \
  --config config.toml \
  --executable .venv/bin/epd-dashboard
```

它会创建用户级 LaunchAgent，在登录后运行，并按 `sync_interval_seconds`
周期尝试同步。卸载：

```bash
.venv/bin/epd-dashboard uninstall-autosync
```

## 7. 已知限制

- LaunchAgent 只在用户登录时运行，不保证电脑休眠或蓝牙关闭时执行。
- 设备必须处于蓝牙范围内；本客户端不提供远程 BLE 网关。
- 本仓库不包含固件、NAS/GitHub 数据生产端或 GitHub Token。
