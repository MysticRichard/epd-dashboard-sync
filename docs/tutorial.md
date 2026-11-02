# 从零跑通 EPD Dashboard Sync

这份教程按“先渲染、再发现、后写入”的顺序走，尽量把硬件风险留在最后。

## 0. 准备什么

- 一台 macOS 或 Windows 电脑；
- Python 3.11+；
- 一块已刷好 EPD-nRF5 槽位协议固件、能被 BLE 扫描到的电子墨水屏；
- 一个脱敏 `dashboard.json`，或先用仓库里的示例文件；
- 如果要显示 GitHub 数据，还需要一个能访问 GitHub 的生产端；本仓库不包含它。

先不要急着写设备。下面的步骤会先验证渲染和配置，再连接 BLE。

## 1. 安装

macOS / Linux：

```bash
git clone <repository-url>
cd epd-dashboard-sync
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,ble]'
cp config.example.toml config.toml
```

Windows PowerShell：

```powershell
git clone <repository-url>
cd epd-dashboard-sync
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,ble]"
Copy-Item config.example.toml config.toml
```

如果只做渲染、不连设备，可以只安装 `.[test]`，不安装 `ble` 额外依赖。

## 2. 先渲染示例

```bash
epd-dashboard render \
  --feed examples/dashboard.example.json \
  --output rendered \
  --profile 4.2_400_300_three_color
```

Windows 把 `epd-dashboard` 换成
`.\.venv\Scripts\epd-dashboard.exe`。

预期得到：

- `rendered/codex.png`
- `rendered/github.png`

打开两张图检查：中文字体是否正常、数字有没有溢出、热力图是否在右侧区域。
如果这里就不对，先不要连设备。

## 3. 准备自己的 feed

### 方式 A：本机 Codex 快照

```bash
epd-dashboard codex-snapshot --config config.toml
```

它会读取 `codex_home` 下的用量计数和限额元数据，写出
`local_snapshot_output`。`sync-once` 会在快照新鲜时覆盖 feed 中的额度窗口和
Token 数据。

### 方式 B：自己的 producer

让 producer 输出脱敏 JSON，并放到 `feed_path`。至少包含：

- `generatedAt`：带时区的 ISO 8601 时间；
- `quota.fiveHour` / `quota.weekly`：剩余百分比和重置时间；
- `codex.tokenUsage`：可用时提供 `total`、`last7Days`、`observedAt` 和 `source`；
- `github.contributions`：贡献天数与总数；
- `repositories`：最多 5 个仓库的摘要。

完整字段参考 [examples/dashboard.example.json](../examples/dashboard.example.json)。
生产端不要把 GitHub Token、NAS 密码、原始 API 响应或聊天正文写进 feed。

### 方式 C：只做静态展示

把 `feed_path` 指向一个本地 JSON 文件，手动更新它。适合先验证屏幕版式，不
适合长期自动刷新。

## 4. 只读发现设备

保持 `slots.configured = false`：

```bash
epd-dashboard discover --config config.toml
```

如果成功，会看到类似：

```text
03 400x300 three_color v2 MTU=... slots=...
```

记录驱动、分辨率、颜色模式、协议版本、槽位数量和当前已用槽位。不要在这一步
猜测设备型号，也不要把未验证的驱动加入配置。

## 5. 配置槽位

在 `config.toml` 中：

```toml
[device]
identifier = "<discover 输出的设备标识>"

[slots]
codex = 0
github = 1
protected = [12, 13, 14]
configured = true
```

原则：

- `codex` 和 `github` 必须是两个不同的、你明确授权的槽位；
- 已被其他内容占用且不想覆盖的槽位放进 `protected`；
- 槽位索引从 0 开始；
- 不确定时保持 `configured = false`，先只读查询。

## 6. 第一次真实写入

设备唤醒、靠近电脑，先做合成看板：

```bash
epd-dashboard sync-once --config config.toml --only codex --combined
```

如果成功，日志会显示 `codex updated`；如果数据没有变化，会显示
`skipped_reason=unchanged`。后者不是失败。

检查设备：

- 新图是否完整，没有缺行或颜色错位；
- 额度、Token、GitHub 数据是否与源数据一致；
- 设备回读的槽位清单是否包含目标槽位；
- 其他槽位是否没有被改动。

如果失败，保留输出图片和日志，不要连续重试。先检查 feed 新鲜度、BLE 连接和
槽位清单。

## 7. 自动同步

### macOS

```bash
epd-dashboard install-autosync \
  --config config.toml \
  --executable .venv/bin/epd-dashboard
```

卸载：

```bash
epd-dashboard uninstall-autosync
```

### Windows

```powershell
.\scripts\windows-sync.ps1 -RepoRoot $PWD
.\scripts\install-windows-task.ps1 -RepoRoot $PWD
```

卸载：

```powershell
.\scripts\install-windows-task.ps1 -RepoRoot $PWD -Uninstall
```

Windows 任务每 30 分钟运行一次，只在当前用户登录时执行。日志在
`state\windows-sync.log`。

## 8. 排障顺序

1. `epd-dashboard render` 能否生成 PNG？
2. `discover` 能否识别设备？
3. feed 的 `generatedAt`、`codex.tokenUsage.observedAt` 是否新鲜？
4. 槽位是否已配置、是否在 `protected` 中？
5. BLE 写入后是否收到 `ready=1`？
6. 设备回读是否出现目标槽位？
7. Windows 是否只看计划任务返回码而忽略了日志？

更多问题见 [troubleshooting.md](troubleshooting.md)。

## 9. 安全清单

- `config.toml` 没有密码、Token 或 Cookie；
- `state/`、`logs/`、`rendered/` 没有提交到 Git；
- feed 中没有原始 API 响应、聊天正文或认证头；
- GitHub PAT 只存在于生产端，权限限制到必要仓库；
- 首次写入前已确认槽位和保护槽位；
- 设备只接收图片，不直接访问数据源。
