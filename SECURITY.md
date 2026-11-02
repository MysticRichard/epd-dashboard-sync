# Security Policy

## 报告问题

请不要在公开 Issue 中粘贴 Token、Cookie、`auth.json`、私有 feed 路径、原始
API 响应或设备日志中的敏感字段。优先使用 GitHub Security Advisories 私下报告，
或在 Issue 中只提供脱敏后的复现步骤。

## 敏感数据边界

本项目客户端只需要脱敏后的 `dashboard.json`。GitHub Token、NAS 凭据和 Codex
登录信息应留在数据生产端，不应出现在客户端配置、Git 仓库、截图或日志中。

## 支持范围

安全修复优先覆盖当前 `main` 分支和最新 release。历史版本可能不再接收修复。
