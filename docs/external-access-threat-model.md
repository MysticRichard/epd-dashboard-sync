# 外网访问与安全边界

本仓库的客户端只做三件事：

1. 读取本机或只读共享上的脱敏 `dashboard.json`；
2. 在本机渲染 PNG；
3. 通过 BLE 写入明确授权的 EPD 槽位。

它不需要、也不应接触以下内容：

- Codex `auth.json`、Cookie、刷新令牌；
- GitHub Personal Access Token；
- NAS 密码、SMB 凭据、SSH 私钥；
- Codex App Server 原始响应或聊天正文；
- 设备完整状态目录或 Docker daemon。

## 如果增加 HTTP 数据源

本仓库不包含 HTTP 服务。若你在生产端增加 `/dashboard.json` 端点，至少应：

- 在反向代理终止 TLS；
- 使用短期 bearer 凭证或 mTLS，不把 token 放进 URL；
- 只允许指定客户端来源；
- 设置请求大小、速率限制和缓存策略；
- 对日志中的认证头、路径和查询参数做脱敏；
- 防火墙默认拒绝公网入站；
- 先在第二台 LAN 主机验证 schema、过期拒绝和响应大小限制。

## 设备边界

EPD 只接收渲染后的图片，不应直接访问数据源、Docker daemon 或 Codex App
Server。设备离线时，客户端应保留上一次有效图片，而不是写入空数据或猜测值。
