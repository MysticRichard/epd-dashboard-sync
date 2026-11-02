# 常见问题

## 找不到设备

- 唤醒设备并放在电脑附近；
- 确认蓝牙已打开，终端/Python 已获得蓝牙权限；
- 确认 `device.service_uuid` 与固件一致；
- 先运行只读命令：

  ```bash
  epd-dashboard discover --config config.toml
  ```

## 看板数据过期或缺失

检查 `feed_path` 是否可读、`generatedAt` 是否在
`stale_after_seconds` 范围内，以及 `dataWarnings` 是否包含
`codex_token_usage_unavailable`、`github_auto_select_unavailable` 等降级标记。

看板文件的时间新，不代表 Codex Token 数据新。还要检查
`codex.tokenUsage.observedAt`、`available` 和 `source`。

## GitHub 区域为空

- 确认 feed 中有 `github.contributions.lastYear` 和 `repositories`；
- 确认生产端成功获取 GitHub 数据；
- 如果只配置了私有仓库，GitHub 账号需要开启私有贡献显示，Token 需要具备
  用户级读取权限；
- 数据不可用时，渲染器会显示未验证状态，而不是伪造 0。

## Codex 额度与网页不一致

额度窗口和 Token 总量是两条独立数据链。分别检查：

- `quota.fiveHour` / `quota.weekly`；
- `codex.tokenUsage.total` / `last7Days`；
- 本机 `state/codex-snapshot.json`；
- `codex.tokenUsage.observedAt` 和 `source`。

如果使用 NAS 或远程生产端，还要确认生产端自身已经刷新，而不是只检查客户端
feed 文件的修改时间。

## 未知驱动

不要猜 profile。先运行 `discover`，确认驱动 id 在代码支持范围内：

- `03`：4.2 英寸 400×300 三色；
- `05`：4.2 英寸 400×300 四色；
- `13`：3.98 英寸 768×552 四色。

不在表中的设备需要先补充经过验证的 profile，不能直接写入。

## 图片已提交但屏幕没变化

检查 `activate_after_sync`：

- `true`：Codex 槽位使用“保存并刷新”，立即显示；
- `false`：只保存槽位，等待设备轮播或手动切换。

## 传输失败

同步器发送提交命令后会等待设备的 `ready=1` 通知。没有收到确认时，不应把
本次写入视为成功。保留日志和输出图片，先只读检查槽位清单，再决定是否重试。

## Windows 计划任务显示成功但设备没更新

这是隐藏 VBS 启动器的已知限制：计划任务的 `LastTaskResult` 可能为 `0`，
但 PowerShell 子进程可能失败。请检查：

```text
state\windows-sync.log
```

日志中的 `exit=`、`FeedError`、`codex updated`、`skipped_reason` 比任务状态更可靠。

## 如何停止自动同步

macOS：

```bash
epd-dashboard uninstall-autosync
```

Windows：

```powershell
.\scripts\install-windows-task.ps1 -RepoRoot $PWD -Uninstall
```

卸载自动任务不会删除设备上已有的图片，也不会删除 `config.toml` 或状态文件。
