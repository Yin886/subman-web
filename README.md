# subman-web — 订阅管理网页后台

手机浏览器可用的订阅管理面板, 部署在 Vercel 上。

## 功能

- 订阅增删改查: 远程订阅 URL / 粘贴节点(内联订阅)
- 一键拉取更新远程订阅并缓存
- 发布: 按所选订阅合并、去重(可关)、输出通用 base64 订阅
- 公开订阅链接 `/s/<token>`, token 即权限, 可随时换新(旧链接失效)

## 路由

| 路由 | 说明 |
|---|---|
| `/` 或 `/admin` | 管理后台(需登录, HTTP Basic Auth) |
| `/api/subs` | 订阅 CRUD + `?action=fetch` 拉取 |
| `/api/bundle` | 发布配置 + `?action=token` 换新链接 |
| `/s/<token>` | 公开订阅内容(无需登录) |

## 环境变量

| 变量 | 说明 |
|---|---|
| `ADMIN_USER` / `ADMIN_PASS` | 后台登录账号密码 |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` | Vercel KV 自动注入, 用作数据存储 |

## 备注

- 远程订阅若为 Clash YAML 格式, 网页面板暂不支持(会明确提示), 可用本地 `substore.py` 转换。
- 节点解析逻辑 vendored 自 `airportlib.py`(仅标准库), 支持 ss/ssr/vmess/vless/trojan/anytls/tuic/hysteria2/socks/http。
