# 排障

所有 helper 错误消息自带 cloudflared 日志尾部（最后 25 行），先读错误再查日志。

## `cloudflared` 未安装或不在 PATH

检查：

```bash
cloudflared --version
which cloudflared
```

安装：macOS `brew install cloudflared`；Windows `winget install --id Cloudflare.cloudflared`；Linux 按发行版参考 Cloudflare 官方包。

已安装但 `which` 找不到：常见于 Homebrew/本地安装路径未加入 PATH（如 `~/.local/bin`、`/opt/homebrew/bin`）。补充 PATH 或使用绝对路径后重试。

## 没有出现 `trycloudflare.com` URL

用 HTTP/2 + 空配置手动重试：

```bash
printf '' > /tmp/cloudflared-empty.yml
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

helper 正在运行时查看日志：

```bash
tail -n 80 .cloudflare-tunnel/quick.log
```

若日志出现瞬时错误标记（`failed to request quick Tunnel`、`context deadline exceeded`、`Client.Timeout exceeded`），helper 会自动重试；手动模式可再跑一次。

## 公网 URL 返回 404

现有 Cloudflare 配置可能干扰 Quick Tunnel 的 ingress 规则。务必使用空配置文件：

```bash
cloudflared --config /tmp/cloudflared-empty.yml tunnel --no-autoupdate --protocol http2 --url http://localhost:<PORT>
```

## 公网 URL 返回 502 / Bad Gateway

Cloudflare 已连到隧道连接器，但本地服务没响应。先修本地：

```bash
curl -I http://localhost:<PORT>
```

若 `localhost` 连不上但 `127.0.0.1` 可以：应用只绑定了 IPv4，而 `localhost` 先解析到 `::1`。helper 已自动处理此回退；手动模式请改用 `http://127.0.0.1:<PORT>`。

## 本地 HTTPS 是自签名证书

```bash
cloudflared tunnel --no-autoupdate --protocol http2 --no-tls-verify --url https://localhost:<PORT>
```

helper 对应参数：`quick --url https://localhost:<PORT> --no-tls-verify`。

## 手机打不开

确认手机打开的是公网 URL：

```text
https://xxxx.trycloudflare.com
```

而不是：

```text
http://localhost:<PORT>
```

另外确认手机与电脑在同一网络下能访问公网（手机可用流量测试），并检查本地服务没有绑定仅本机可见的防火墙规则。

## 验证失败 / Could not resolve host（exit 6）

系统 DNS 解析不了新子域（常见于 fake-IP 代理 DNS，或 trycloudflare 传播未完成）。用 DNS-over-HTTPS 重试：

```bash
curl --noproxy '*' --doh-url https://1.1.1.1/dns-query -sS -L --max-time 20 https://xxxx.trycloudflare.com
```

helper 的 `verify` 已内置此回退。DNS 传播可能需 1-2 分钟，先重试再判定故障。

## Named 隧道 DNS 失败

```bash
cloudflared tunnel list
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

确认域名在 Cloudflare 处于 active、主机名未被其他 DNS 记录占用、tunnel 名称与配置一致。

## 登录不弹浏览器

```bash
cloudflared tunnel login
```

把打印的 URL 手动粘到浏览器，选择正确的 Cloudflare zone，然后重跑后续命令。

## `named-run` 报找不到配置

先 `named-config` 生成配置，或在 `named-run` 传入 `--hostname` 让它自动生成。确认 `<name>.yml` 在 `.cloudflare-tunnel/`（或 `CFT_STATE_DIR` 指向的目录）。

## 状态目录进了 git

`.cloudflare-tunnel/` 含 PID、URL 和日志，是运行时产物，不应提交。在仓库 `.gitignore` 中加入：

```text
.cloudflare-tunnel/
```

## 代理 / VPN / WARP 干扰

系统代理或 VPN 可能劫持对 trycloudflare 的连接。手动验证请用 `curl --noproxy '*'`；helper 的 `fetch` 已默认 `--noproxy '*'`。若开了 WARP 且公网访问异常，可临时关闭 WARP 再测。
