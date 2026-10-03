# 安全

Cloudflare Tunnel 把本地服务暴露到公网。除非配置了 Cloudflare Access 或应用级认证，否则拿到公网 URL 的任何人（包括爬虫和扫描器）都能访问该服务。

## 暴露前必须与用户确认

以下内容**禁止**未经确认直接暴露：

- 后台管理界面 / 管理控制台
- 数据库控制台、Docker daemon API
- Redis、Postgres、MySQL、Elasticsearch 等数据服务
- 内网系统、内部工具
- 含 API key、cookie、bearer token、会话转储、私密文件的应用
- 无认证的写接口（POST/PUT/DELETE 可改数据）
- 开启调试控制台的本地开发应用

## 更安全的行为

- Quick 模式只用于短时 demo，演示完即 `stop`。
- 需要稳定入口的内部工具：Named 模式 + Cloudflare Access。
- 不暴露裸数据库或私网服务。
- 不把生成的 Cloudflare 凭据或 tunnel token 提交进仓库。
- 分享链接前先 `verify` 公网 URL 返回的内容。

## Cloudflare Access

敏感应用需要稳定域名时，建议先用 Cloudflare Access 或应用级认证保护，再分享 URL。

本 skill 可以帮忙创建隧道，但**不应**在未做访问控制决策的情况下静默发布敏感服务。
