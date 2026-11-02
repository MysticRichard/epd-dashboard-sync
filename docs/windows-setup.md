# Windows 安装与自动同步

本文只描述 Windows 客户端。完整背景和通用步骤见 [README](../README.md) 与
[教程](tutorial.md)。

## 1. 安装

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,ble]"
Copy-Item config.example.toml config.toml
```

## 2. 配置数据源

把 `config.toml` 中的 `feed_path` 指向一个脱敏 `dashboard.json`。可以是：

- 本地文件，例如 `C:\Users\<you>\epd-feed\dashboard.json`；
- 只读 SMB/UNC 路径，例如 `\\<NAS_HOST>\<SHARE_NAME>\dashboard.json`；
- 首次试跑时使用仓库自带示例：`./examples/dashboard.example.json`。

不要在 `config.toml` 中写 NAS 密码、GitHub Token、Cookie 或 Codex 登录凭据。
Windows 使用当前用户的 SMB 会话读取共享文件；凭据应由系统凭据管理器管理。

## 3. 只读发现设备

```powershell
.\.venv\Scripts\epd-dashboard.exe discover --config .\config.toml
```

确认输出中的驱动、分辨率、颜色模式、协议版本和槽位数量与你的设备一致。
首次运行前保持 `slots.configured = false`。

## 4. 授权槽位

在 `config.toml` 中设置两个不同的槽位，并把实际存在的其他槽位放入
`slots.protected`。确认无误后设置：

```toml
[slots]
configured = true
```

不要猜测槽位。写错槽位会覆盖设备上的旧图片。

## 5. 手动同步一次

```powershell
.\scripts\windows-sync.ps1 -RepoRoot $PWD
```

脚本会先刷新本机脱敏 Codex 快照，再读取 `feed_path`，渲染并写入设备。
如果本机 Codex App Server 不可用，会尝试使用已有的新鲜快照或 feed 回退；
过期或不可用的数据不会覆盖设备。

日志写入 `state\windows-sync.log`。先确认日志中有 `codex updated` 或
`skipped_reason=unchanged`，再检查设备画面。

## 6. 安装计划任务

手动同步成功后：

```powershell
.\scripts\install-windows-task.ps1 -RepoRoot $PWD
```

任务每 30 分钟运行一次，只在当前用户登录时执行，使用隐藏窗口和全局互斥锁。
卸载：

```powershell
.\scripts\install-windows-task.ps1 -RepoRoot $PWD -Uninstall
```

## 7. 已知限制

- `windows-sync-hidden.vbs` 使用 `WScript.Shell.Run` 启动 PowerShell，Windows
  计划任务可能显示 `0` 而子进程实际失败。排障必须查看
  `state\windows-sync.log`，不能只看 `LastTaskResult`。
- 设备必须处于蓝牙范围内且可被扫描；Windows 休眠、蓝牙关闭或设备离线时不会写入。
- 本仓库不包含固件、NAS/GitHub 数据生产端或 GitHub Token。
