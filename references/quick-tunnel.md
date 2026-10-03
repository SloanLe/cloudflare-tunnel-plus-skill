# Quick Tunnel（临时预览）

Quick Tunnel 为本地 HTTP/HTTPS 服务生成临时 `https://*.trycloudflare.com` 公网链接。

## 适用场景

- 本地应用预览、demo、课堂分享
- 短期客户联调
- 不需要稳定域名的 webhook 测试

## 不适用场景

- 生产流量、固定回调地址
- 大文件分发、对可用性有承诺的服务
- 依赖 Server-Sent Events（SSE）等长连接特性的流式功能

## 推荐流程

1. 确定本地服务 URL（`http://localhost:<端口>`）。
2. helper 自动验证本地服务可达；`localhost` 解析失败时自动回退 `127.0.0.1`。
3. 用 HTTP/2 + 空配置启动隧道。
4. 等待公网 URL 与 `Registered tunnel connection`。
5. 验证公网 URL 返回预期内容。
6. 明确告知用户链接是临时的。

## Helper 命令

```bash
python3 <skill_dir>/scripts/tunnel_helper.py quick --url http://localhost:<PORT>
python3 <skill_dir>/scripts/tunnel_helper.py quick --port 3000
python3 <skill_dir>/scripts/tunnel_helper.py quick --url https://localhost:8443 --no-tls-verify
python3 <skill_dir>/scripts/tunnel_helper.py status
python3 <skill_dir>/scripts/tunnel_helper.py stop
```

运行状态写入当前目录 `.cloudflare-tunnel/`（环境变量 `CFT_STATE_DIR` 可覆盖，适合多项目或全局管理）：

- `quick.pid` / `quick.log` / `quick-url.txt` / `quick-local-url.txt` / `quick-target-url.txt` / `cloudflared-empty.yml`

行为：

- 同目录对相同本地 URL 重复执行 → 复用已有隧道（`"reused": true`）。
- 同目录对不同本地 URL 执行 → 先停旧隧道再启新。
- `--no-reuse` 强制停旧启新。
- 瞬时 `api.trycloudflare.com` 失败自动重试最多 3 次。

## 手动命令

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

自签名本地 HTTPS：

```bash
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --no-tls-verify --url https://localhost:8443
```

## 验证

```bash
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "预期内容"
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --expect-status 200
```

`verify` 在系统 DNS 解析失败（curl exit 6，常见于 fake-IP 代理 DNS 或新子域传播未完成）时自动改用 DNS-over-HTTPS `https://1.1.1.1/dns-query` 重试。新 trycloudflare 子域 DNS 传播可能需 1-2 分钟：先重试，再判定隧道故障。

## 注意事项

Quick Tunnel URL 是临时的：进程重启后变化；机器休眠、断网或 cloudflared 退出后失效。手机等外部设备应打开公网 URL，而不是 `localhost:<端口>`。
