# Named Tunnel（固定域名）

Named Tunnel 把固定主机名（如 `app.example.com`）通过 Cloudflare Tunnel 映射到本地服务。

## 适用场景

- 需要稳定公网 URL
- webhook 注册（GitHub、Slack、Stripe、n8n 等）
- 可重复的客户预览、长期公网映射

## 前置条件

- 已安装 `cloudflared`
- 有 Cloudflare 账号
- 域名 DNS 已托管到 Cloudflare
- 能完成浏览器登录，或通过 Cloudflare 正规途径提供已授权的 tunnel token

## 本地托管流程（完整生命周期）

登录（交互式浏览器；不要打印/保存 token）：

```bash
cloudflared tunnel login
```

创建隧道并绑定 DNS：

```bash
cloudflared tunnel create <TUNNEL_NAME>
cloudflared tunnel route dns <TUNNEL_NAME> <HOSTNAME>
```

生成配置并后台运行：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py named-config \
  --name <TUNNEL_NAME> --hostname <HOSTNAME> --url http://localhost:<PORT>

python3 <skill_dir>/scripts/tunnel_helper.py named-run \
  --name <TUNNEL_NAME> --url http://localhost:<PORT> --hostname <HOSTNAME>
```

- `named-run` 后台启动，等待边缘连接注册成功后返回，PID 与日志记录在 `.cloudflare-tunnel/`。
- 配置缺失时传入 `--hostname` 会自动生成配置；不传则要求先 `named-config`。
- `--credentials-file` 可指定云端凭据 JSON。

管理：

```bash
python3 <skill_dir>/scripts/tunnel_helper.py named-status --name <TUNNEL_NAME>
python3 <skill_dir>/scripts/tunnel_helper.py named-stop --name <TUNNEL_NAME>
python3 <skill_dir>/scripts/tunnel_helper.py list
```

状态文件（`.cloudflare-tunnel/`，可用 `CFT_STATE_DIR` 覆盖）：`<name>.yml`（配置）、`<name>.pid`、`<name>.log`、`<name>.hostname.txt`。

## 配置形态

helper 生成的配置：

```yaml
tunnel: <TUNNEL_NAME>

ingress:
  - hostname: app.example.com
    service: http://localhost:3000
  - service: http_status:404
```

多数本地场景无需 credentials-file，`cloudflared` 会从用户既有 Cloudflare 配置解析凭据。若需要：

```yaml
credentials-file: /Users/<user>/.cloudflared/<TUNNEL_ID>.json
```

## Token 方式（远程托管隧道）

若用户在 Cloudflare Dashboard 创建了远程托管隧道，会得到 token 运行命令：

```bash
cloudflared tunnel run --token <TOKEN>
```

不要把 token 打印、提交或写入仓库文件；需要自动化时放入环境变量或本地密钥管理器。

## 验证

先验证本地服务：

```bash
curl -I http://localhost:<PORT>
```

再验证公网主机名：

```bash
curl -I https://<HOSTNAME>
python3 <skill_dir>/scripts/tunnel_helper.py verify --url https://<HOSTNAME> --expect-status 200
```

DNS 路由刚完成后主机名可能短暂不可达，稍等重试；DNS 与边缘传播需要时间。
