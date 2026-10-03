---
name: cloudflare-tunnel-plus
description: "用 Cloudflare Tunnel 把本地 HTTP/HTTPS 服务暴露到公网。当用户需要分享 localhost、生成临时 trycloudflare.com 预览链接、把固定自定义域名映射到本地服务、为 webhook 提供公网入口，或需要检查/验证/排查隧道时使用。覆盖完整生命周期：环境检查、Quick 隧道、Named 隧道、状态查询、停止、列表、公网 URL 验证与排障。Expose a local service to the public internet via Cloudflare Tunnel: temporary quick tunnels, fixed custom-domain named tunnels, full lifecycle management, verification and troubleshooting."
license: MIT
metadata:
  tags: [cloudflare, tunnel, cloudflared, localhost, public-url, preview, custom-domain, webhook, 内网穿透]
---

# Cloudflare Tunnel Plus

把本地服务安全地暴露到公网。先选模式，再做安全检查，再执行。

## 1. 选择模式

- **Quick 模式**：临时 `https://*.trycloudflare.com` 链接。用于 demo、预览、课堂分享、短期联调。URL 重启后变化，进程退出/电脑休眠/断网即失效。
- **Named 模式**：固定域名（如 `app.example.com -> http://localhost:3000`）。用于 webhook、长期预览、稳定公网入口。前提：已登录 Cloudflare 账号且域名 DNS 托管在 Cloudflare。

判断规则：用户说"临时/预览/分享/快速给个链接"→ Quick；说"固定域名/稳定地址/webhook/长期"→ Named。拿不准且服务不敏感时默认 Quick。

## 2. 路径与状态

所有 `scripts/...` 路径相对本 skill 安装目录。先定位安装目录（包含本 SKILL.md 的文件夹，通常在 `workspace/.user_skills/cloudflare-tunnel-plus/`），再调用：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py <命令>
```

helper 把运行状态写入当前工作目录的 `.cloudflare-tunnel/`（可用环境变量 `CFT_STATE_DIR` 覆盖），因此 `status`/`stop`/`list` 必须在启动隧道的同一目录运行。若当前目录是 git 仓库，提醒用户把 `.cloudflare-tunnel/` 加入 `.gitignore`。

## 3. 前置检查

```bash
python3 <skill_dir>/scripts/tunnel_helper.py check
```

- `cloudflared` 缺失时：macOS `brew install cloudflared`；Windows `winget install --id Cloudflare.cloudflared`；Linux 用 Cloudflare 官方包。装完重新 check。
- 本地服务验证由 helper 自动完成（`curl -I` 兜底：`200/301/302/304/401/403` 视为可达）。若用户给的地址是 `localhost` 而服务只绑定 IPv4，helper 会自动回退到 `127.0.0.1`。

## 4. Quick 隧道（临时预览）

```bash
python3 <skill_dir>/scripts/tunnel_helper.py quick --url http://localhost:3000
# 或 --port 3000；自签名 HTTPS 加 --no-tls-verify
```

- 同一目录对**相同**本地 URL 重复执行会复用已有隧道（`"reused": true`）；不同 URL 会先停旧再启新。加 `--no-reuse` 强制重启。
- 瞬时失败自动重试最多 3 次。

查询与停止：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py status
python3 <skill_dir>/scripts/tunnel_helper.py stop
```

手动兜底（helper 异常时）：

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

## 5. Named 隧道（固定域名）

前置：`cloudflared tunnel login`（交互式浏览器登录，不要打印/保存 token）。

```bash
cloudflared tunnel create <TUNNEL_NAME>
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

生成配置并后台运行：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py named-config --name <TUNNEL_NAME> --hostname <HOSTNAME> --url http://localhost:<PORT>
python3 <skill_dir>/scripts/tunnel_helper.py named-run --name <TUNNEL_NAME> --url http://localhost:<PORT> --hostname <HOSTNAME>
```

`named-run` 会后台启动、等边缘连接注册成功、记录 PID 并输出日志路径；配置缺失时传入 `--hostname` 会自动生成。管理命令：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py named-status --name <TUNNEL_NAME>
python3 <skill_dir>/scripts/tunnel_helper.py named-stop --name <TUNNEL_NAME>
```

Dashboard 远程托管隧道（token 方式）只能手动前台运行：`cloudflared tunnel run --token <TOKEN>`，不要把 token 写进仓库或配置文件。

## 6. 全部隧道一览

```bash
python3 <skill_dir>/scripts/tunnel_helper.py list
```

输出当前目录管理的所有 Quick/Named 隧道及其运行状态。

## 7. 验证公网 URL

```bash
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "预期内容"
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://app.example.com --expect-status 200
```

`verify` 在系统 DNS 解析不了新子域时自动走 DNS-over-HTTPS（`1.1.1.1`）。新 trycloudflare 域名 DNS 传播可能需要 1-2 分钟，先重试再判定隧道故障。

## 8. 安全检查（发布前必做）

暴露敏感服务前先读 `references/security.md` 并向用户确认。以下内容**禁止**直接暴露：后台管理界面、数据库/Docker/Redis 等裸服务、含 token/API key/私密文件的应用、无认证的写接口、内网系统。敏感服务先加应用登录或 Cloudflare Access。

## 9. 输出格式

成功交付时返回：公网 URL、本地 URL、使用模式、停止命令、Quick 模式附短暂性提醒：

```text
Public URL: https://xxxx.trycloudflare.com
Local URL: http://localhost:3000
Mode: quick
Stop: python3 <skill_dir>/scripts/tunnel_helper.py stop

这是临时 Quick Tunnel 链接：cloudflared 退出、电脑休眠或断网后即失效。
```

## 10. 排障

按症状查阅 `references/troubleshooting.md`：无公网 URL、404、502、自签名 HTTPS、手机打不开、named DNS/登录失败、cloudflared 不在 PATH、localhost IPv6 问题等。所有错误消息自带 `cloudflared` 日志尾部，先读错误再查日志。
