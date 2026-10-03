# Cloudflare Tunnel Plus Skill

[English](README_EN.md) | 中文

一个给 AI Agent 使用的 **Cloudflare Tunnel workflow skill**，它可以把本地 HTTP/HTTPS 服务暴露到公网，支持两种模式：

- **Quick Tunnel**：临时 `https://*.trycloudflare.com` 预览链接，适合 demo、课堂、客户临时预览。
- **Named Tunnel**：固定域名映射，例如 `app.example.com -> http://localhost:3000`，适合 webhook、长期预览、团队测试入口。

## 功能亮点

| 能力 | 常规做法 | 本 skill |
|---|---|---|
| Named 隧道 | 只能手动前台运行 | `named-run` / `named-status` / `named-stop` 后台全生命周期管理 |
| 进程终止 | `killpg` 失败无降级（真实 bug） | 降级到直接 `os.kill`，测试覆盖 |
| localhost 解析 | 失败即报错 | 自动回退 `127.0.0.1`（解决应用只绑 IPv4 的问题） |
| 错误诊断 | 只给日志路径 | 错误消息自带 `cloudflared` 日志尾部 25 行 |
| URL 验证 | 仅 2xx-4xx 判定 | 新增 `--expect-status`、`--contains` |
| 隧道管理 | 无 | 新增 `list` 一览全部隧道、`--no-reuse`、`CFT_STATE_DIR` 状态目录覆盖、URL 自动补 scheme、陈旧 PID 清理 |
| 适配 | 面向 Claude Code 路径 | 豆包 / Claude Code / Codex 通用，脚本路径运行时自解析 |

## 适合场景

- 把 `localhost:3000` 临时发给别人看
- 给本地 API 创建临时公网 URL
- 用固定域名访问本地开发服务
- 给 GitHub、Slack、Stripe、n8n 等 webhook 配公网入口
- 让 Agent 自动检查本地服务、启动 tunnel、验证公网 URL、随时停止

## 不适合场景

- 没有认证保护的敏感后台
- 数据库、Redis、Docker API 等裸服务
- 大文件下载站或公开网盘
- 不愿意接受本地电脑断网/睡眠导致服务中断的生产服务

## 安装

> Agent 只识别 `<skill根>/<名字>/SKILL.md` 这种**目录结构**，helper 脚本必须随 skill 一起安装，请完整复制 `SKILL.md` + `scripts/` + `references/` 三部分。

豆包（Doubao）用户 skill 目录：

```bash
git clone https://github.com/<你的用户名>/cloudflare-tunnel-plus-skill /tmp/cft-plus
mkdir -p <你的workspace>/.user_skills/cloudflare-tunnel-plus
cp -R /tmp/cft-plus/SKILL.md /tmp/cft-plus/scripts /tmp/cft-plus/references \
      <你的workspace>/.user_skills/cloudflare-tunnel-plus/
rm -rf /tmp/cft-plus
```

Claude Code 全局安装（所有项目可用）：

```bash
git clone https://github.com/<你的用户名>/cloudflare-tunnel-plus-skill /tmp/cft-plus
mkdir -p ~/.claude/skills/cloudflare-tunnel-plus
cp -R /tmp/cft-plus/SKILL.md /tmp/cft-plus/scripts /tmp/cft-plus/references \
      ~/.claude/skills/cloudflare-tunnel-plus/
rm -rf /tmp/cft-plus
```

> 系统要求：需要预装 `cloudflared`（安装方法见 `references/troubleshooting.md`）和 Python 3。helper 脚本在 macOS / Linux 上经过完整测试；Windows 为尽力支持（进程管理走 `tasklist`/`taskkill`），建议 Windows 用户优先使用 WSL。

## Quick Tunnel 临时链接

```bash
python3 scripts/tunnel_helper.py quick --url http://localhost:3000   # 或 --port 3000
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "预期内容"
```

- 同一目录对相同本地 URL 重复执行会复用已有隧道；不同 URL 会先停旧再启新；`--no-reuse` 强制重启。
- 瞬时失败自动重试最多 3 次；验证失败时自动回退 DNS-over-HTTPS。
- 自签名本地 HTTPS 加 `--no-tls-verify`。

## Named Tunnel 固定域名

```bash
cloudflared tunnel login
cloudflared tunnel create my-app
cloudflared tunnel route dns my-app app.example.com
python3 scripts/tunnel_helper.py named-config --name my-app --hostname app.example.com --url http://localhost:3000
python3 scripts/tunnel_helper.py named-run --name my-app --url http://localhost:3000 --hostname app.example.com
python3 scripts/tunnel_helper.py named-status --name my-app
python3 scripts/tunnel_helper.py named-stop --name my-app
```

## 隧道一览

```bash
python3 scripts/tunnel_helper.py list
```

## 本地测试

端到端 Quick Tunnel 测试（需要 cloudflared 和网络）：

```bash
python3 scripts/test_quick_tunnel.py
```

离线单元测试（不需要 cloudflared / 网络）：

```bash
python3 scripts/test_helper_unit.py
python3 -m py_compile scripts/tunnel_helper.py
python3 scripts/tunnel_helper.py --help
```

## 安全提醒

Tunnel 会把本地服务暴露到公网。发布前先确认没有公开：

- Token、Cookie、API key
- 未加认证的后台
- 内网系统
- 数据库或调试控制台
- 可修改数据的无认证 API

敏感服务请先加应用登录或 Cloudflare Access，详见 `references/security.md`。

## 许可证

MIT，详见 `LICENSE`。
