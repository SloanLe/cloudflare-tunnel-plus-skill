# Cloudflare Tunnel Plus Skill

[English](README_EN.md) | 中文

一个给 AI Agent 使用的 Cloudflare Tunnel workflow skill。把本地 HTTP/HTTPS 服务暴露到公网，支持两种模式：

- Quick Tunnel：临时 https://*.trycloudflare.com 预览链接
- Named Tunnel：固定域名映射（app.example.com -> http://localhost:3000）

## 功能亮点

- Named 隧道后台全生命周期管理（named-run / named-status / named-stop）
- 修复进程终止 killpg 无降级 bug
- localhost 自动回退 127.0.0.1（解决 IPv4-only 绑定）
- 错误消息自带 cloudflared 日志尾部
- verify 新增 --expect-status / --contains
- 新增 list / --no-reuse / CFT_STATE_DIR / 陈旧 PID 清理

## 安装

```bash
git clone https://github.com/<你的用户名>/cloudflare-tunnel-plus-skill /tmp/cft-plus
mkdir -p ~/.claude/skills/cloudflare-tunnel-plus
cp -R /tmp/cft-plus/SKILL.md /tmp/cft-plus/scripts /tmp/cft-plus/references \
      ~/.claude/skills/cloudflare-tunnel-plus/
rm -rf /tmp/cft-plus
```

豆包用户把目标目录换成 `<你的workspace>/.user_skills/cloudflare-tunnel-plus` 即可。

## 使用

```bash
# Quick 临时链接
python3 scripts/tunnel_helper.py quick --url http://localhost:3000
python3 scripts/tunnel_helper.py status
python3 scripts/tunnel_helper.py stop
python3 scripts/tunnel_helper.py verify --url https://xxxx.trycloudflare.com --contains "预期内容"

# Named 固定域名
cloudflared tunnel login
cloudflared tunnel create my-app
cloudflared tunnel route dns my-app app.example.com
python3 scripts/tunnel_helper.py named-config --name my-app --hostname app.example.com --url http://localhost:3000
python3 scripts/tunnel_helper.py named-run --name my-app --url http://localhost:3000
python3 scripts/tunnel_helper.py named-status --name my-app
python3 scripts/tunnel_helper.py named-stop --name my-app

# 一览全部隧道
python3 scripts/tunnel_helper.py list
```

## 测试

```bash
python3 scripts/test_helper_unit.py    # 离线单元测试
python3 scripts/test_quick_tunnel.py   # 端到端（需 cloudflared + 网络）
```

## 许可证

MIT。
