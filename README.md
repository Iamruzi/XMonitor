# XMonitor

XMonitor 是一个基于 `twitter-cli` 改造的 X/Twitter 用户监控平台。它使用你自己的 X 登录 Cookie 拉取数据，提供中文 Web 控制台，用来管理监控用户、监控行为、分组备注、事件列表和通知渠道。

主要入口不是原来的命令行工具，而是：

```bash
twitter-monitor
```

## 功能

- 监控用户原创发推、转推、回复和新增关注
- 每个监控用户可设置分组、备注名、启停状态和监控行为
- 事件中心支持按分组、用户、事件类型、通知结果筛选
- Telegram 通知和 Telegram Bot 管理菜单
- WxPusher HTML 通知，链接可点击
- Bark iOS 通知，支持普通、时效、紧急、持续响铃和自定义铃声
- 外文正文和简介自动尝试翻译成中文
- 前端可配置 Telegram Bot Token、接收聊天、WxPusher AppToken、UID、Bark 设备码、轮询间隔
- 项目雷达展示所有共同关注账号，可筛选疑似项目，并展示证据、火爆等级和早期项目信号
- 新监控用户首次基线会拉取更大关注范围，手动“补齐”可补充已有用户的共同关注关系
- WxPusher/Bark 可只推热点项目
- 支持批量导入和导出监控用户
- 支持后台自动轮询、随机抖动和失败退避
- 支持 systemd 常驻部署和 Tailscale 内网访问

第一次检查只建立基线，不会把历史内容当作新事件推送。后续发现新增内容后，会记录到事件列表，并推送到已配置的通知渠道。

## 本地运行

要求 Python 3.10+，推荐使用 `uv`。

```powershell
cd D:\Works\Deng\twitter-cli

$env:PYTHONUTF8="1"
$env:TWITTER_AUTH_TOKEN="你的 auth_token"
$env:TWITTER_CT0="你的 ct0"

# 可选：本地 Clash 或其他代理
$env:TWITTER_PROXY="http://127.0.0.1:7890"

# 必填：管理密码，只从环境变量读取
$env:MONITOR_ADMIN_TOKEN="换成你自己的密码"

uv sync
uv run twitter-monitor
```

打开：

```text
http://127.0.0.1:8000/
```

登录页使用 `MONITOR_ADMIN_TOKEN` 作为管理密码；未配置时后台会锁定受保护 API。

## 服务器部署

推荐部署在 Tailscale 内网地址上，不暴露公网端口。

示例目录：

```text
/opt/xmonitor/app
/opt/xmonitor/data/xmonitor.db
/etc/xmonitor/xmonitor.env
```

示例环境变量文件 `/etc/xmonitor/xmonitor.env`：

```bash
PYTHONUTF8=1
MONITOR_HOST=100.x.x.x
PORT=8000
MONITOR_DB_PATH=/opt/xmonitor/data/xmonitor.db
MONITOR_TIMEZONE=Asia/Shanghai
MONITOR_BACKGROUND_WORKER=true
MONITOR_POLL_INTERVAL_MIN=180
MONITOR_POLL_INTERVAL_MAX=300
MONITOR_POLL_BACKOFF_MAX=1800
MONITOR_TG_COMMANDS=true
MONITOR_ADMIN_TOKEN=换成强密码
TWITTER_AUTH_TOKEN=你的 auth_token
TWITTER_CT0=你的 ct0
```

systemd 示例：

```ini
[Unit]
Description=X Monitor dashboard
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xmonitor/app
EnvironmentFile=/etc/xmonitor/xmonitor.env
ExecStart=/opt/xmonitor/app/.venv/bin/twitter-monitor
Restart=always
RestartSec=10
UMask=0077

[Install]
WantedBy=multi-user.target
```

部署或更新：

```bash
cd /opt/xmonitor/app
GIT_SSH_COMMAND='ssh -i /root/.ssh/xmonitor_deploy_ed25519' git pull --ff-only
uv sync --frozen --no-dev
systemctl restart xmonitor
```

常用管理命令：

```bash
systemctl start xmonitor
systemctl stop xmonitor
systemctl restart xmonitor
systemctl status xmonitor --no-pager
journalctl -u xmonitor -f
```

健康检查：

```bash
curl http://100.x.x.x:8000/api/health
```

## 代理

如果服务器直连 X、Telegram 或 Google 翻译不通，需要在服务器上提供一个本机代理端口，然后只让 XMonitor 使用它。

推荐使用 Mihomo，只监听本机：

```yaml
mixed-port: 7890
allow-lan: false
external-controller: 127.0.0.1:9090
```

然后在 XMonitor 右上角“设置”里的“网络代理”填写：

```text
http://127.0.0.1:7890
```

这个代理只会被 XMonitor 主动使用，不会影响服务器上的其他服务。不要开启系统全局代理、TUN、iptables 透明代理，除非你明确知道会影响哪些服务。

测试代理：

```bash
curl -x http://127.0.0.1:7890 https://x.com -I
curl -x http://127.0.0.1:7890 https://api.telegram.org -I
curl -x http://127.0.0.1:7890 https://translate.googleapis.com -I
```

## 前端管理

登录后主要分为：

- 用户监控：按行管理用户、用户名、分组、备注、启停状态和监控行为
- 最新事件：按分组、用户、事件类型和通知状态筛选
- 设置：管理通知渠道、Bark 通知级别、轮询间隔、分组、批量导入导出

用户显示逻辑优先使用：

```text
分组｜备注名｜显示名（@用户名）
```

例如：

```text
alpha猎手｜wx好友流星｜流星（@0xliuxing）
```

这样 WxPusher 标题、Bark 标题、Telegram 通知和前端事件列表都能清楚看出是哪一组、哪个人触发了事件。

## Telegram Bot 管理

配置 Telegram Bot Token 和接收聊天后，服务会启动 Telegram 长轮询管理器。默认只接受已授权聊天的命令。

常用命令：

```text
/help                         查看菜单
/status                       查看运行状态
/users                        查看监控用户
/add @用户名                  添加用户
/add @用户名 原创 关注        添加用户并指定行为
/del @用户名                  删除用户
/on @用户名                   启用用户
/off @用户名                  暂停用户
/watch @用户名 原创 开        开启某个行为
/watch @用户名 转推 关        关闭某个行为
/watch @用户名 全部 开        开启全部行为
/meta @用户名 分组 备注名      设置分组和备注
/meta @用户名 - -             清空分组和备注
/groups                       查看已有分组
/group rename 旧分组 新分组    批量改名
/group clear 分组             清空某个分组
/auth me                      查看当前聊天 ID
/auth list                    查看已授权聊天
/auth add 当前                授权当前聊天
/auth add <chat_id> <备注>    授权指定聊天
/auth del <chat_id>           删除授权
/auth rename <chat_id> <备注> 修改授权备注
/wxpusher status              查看 WxPusher 配置
/wxpusher on                  开启 WxPusher 通知
/wxpusher off                 暂停 WxPusher 通知
/wxpusher token <AppToken>    保存 WxPusher AppToken
/wxpusher add <UID>           增加 WxPusher 接收人
/wxpusher del <UID>           删除 WxPusher 接收人
/wxpusher test                发送测试消息
/bark status                  查看 Bark 配置
/bark on                      开启 Bark 通知
/bark off                     暂停 Bark 通知
/bark server https://api.day.app 设置 Bark 服务地址
/bark add <设备码>             增加 Bark 设备码
/bark del <设备码>             删除 Bark 设备码
/bark level 普通|时效|紧急      设置通知级别
/bark sound <铃声名>           设置铃声，用 - 清空
/bark call 开|关               设置紧急持续响铃
/bark volume 0-10             设置紧急音量
/bark test                    发送 Bark 测试消息
```

把 Bot 拉到新群后，先在新群发：

```text
/auth me
```

拿到群 Chat ID 后，在主聊天或已授权聊天里执行：

```text
/auth add <chat_id> <备注>
```

授权后，该群可以执行管理命令，也会收到 Telegram 通知。

## WxPusher 通知

前端设置里可以配置：

- AppToken
- 一个或多个接收人 UID
- 通知开关，关闭后保留配置但不发送 WxPusher 通知

WxPusher 使用 HTML 内容类型。通知中的 X 用户名、关注对象、正文里的 `@用户名` 和普通链接会尽量转成可点击链接。

## Bark 通知

前端设置里可以配置：

- Bark 服务地址，默认 `https://api.day.app`
- 一个或多个设备码
- 通知开关，关闭后保留配置但不发送 Bark 通知
- 推送分组
- 通知级别：静默收纳、普通提醒、时效提醒、紧急提醒
- 铃声名
- 紧急持续响铃和紧急音量

Bark 使用 Markdown 内容。标题会突出事件类型、分组、备注和用户，正文按原文/原简介、翻译、链接分段，X 用户和链接会尽量转成可点击链接。

打开“紧急持续响铃”后，XMonitor 发送 Bark 通知时会按紧急级别处理，并带上 `call=1`。适合你确实要被手机强提醒的监控项。普通监控建议使用“普通提醒”或“时效提醒”，避免手机通知过载。

## 测试通知

前端设置页支持分别发送 Telegram、WxPusher、Bark 测试通知，也支持一次测试全部渠道。单渠道测试不会触发其他渠道，方便排查某个通知配置是否正常。

## 批量导入导出

前端设置支持批量导入监控用户，每行一个：

```text
@0xliuxing,alpha猎手,wx好友流星
SkyAAmen,项目方,重点观察
```

导出为 CSV 后可以修改再导入。默认情况下，已存在用户会更新分组、备注和监控行为，不会重复创建。

## 轮询策略

默认不是固定 1 分钟轮询，而是：

```text
180-300 秒随机等待
```

如果本轮有检查失败，下一轮会自动退避，最长不超过：

```text
1800 秒
```

成功后恢复普通随机区间。轮询区间可以在前端“设置 - 后台检查”里修改。

## 环境变量

| 名称 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `TWITTER_AUTH_TOKEN` | 是 | | X 的 `auth_token` Cookie |
| `TWITTER_CT0` | 是 | | X 的 `ct0` Cookie |
| `TWITTER_PROXY` | 否 | | X 请求代理 |
| `TELEGRAM_BOT_TOKEN` | 否 | | Telegram Bot Token，也可前端配置 |
| `TELEGRAM_CHAT_ID` | 否 | | Telegram 接收聊天，也可前端配置 |
| `TELEGRAM_PROXY` | 否 | `TWITTER_PROXY` | Telegram 代理 |
| `WXPUSHER_APP_TOKEN` | 否 | | WxPusher AppToken，也可前端配置 |
| `WXPUSHER_UIDS` | 否 | | WxPusher UID，多个用逗号分隔 |
| `BARK_SERVER_URL` | 否 | `https://api.day.app` | Bark 服务地址 |
| `BARK_DEVICE_KEY` | 否 | | 单个 Bark 设备码 |
| `BARK_DEVICE_KEYS` | 否 | | 多个 Bark 设备码，逗号分隔 |
| `BARK_LEVEL` | 否 | `active` | Bark 级别：`passive`、`active`、`timeSensitive`、`critical` |
| `BARK_SOUND` | 否 | | Bark 铃声名 |
| `BARK_GROUP` | 否 | `XMonitor` | Bark 推送分组 |
| `BARK_CALL` | 否 | `false` | 是否启用 Bark 持续响铃 |
| `BARK_VOLUME` | 否 | `5` | Bark 紧急音量，0-10 |
| `MONITOR_HOST` | 否 | `0.0.0.0` | 服务监听地址 |
| `PORT` | 否 | `8000` | 服务端口 |
| `MONITOR_DB_PATH` | 否 | `twitter-monitor.db` | SQLite 数据库路径 |
| `MONITOR_ADMIN_TOKEN` | 是 | | Web 管理密码 |
| `MONITOR_BACKGROUND_WORKER` | 否 | `true` | 是否启用后台轮询 |
| `MONITOR_POLL_INTERVAL_MIN` | 否 | `180` | 最短轮询等待，秒 |
| `MONITOR_POLL_INTERVAL_MAX` | 否 | `300` | 最长轮询等待，秒 |
| `MONITOR_POLL_BACKOFF_MAX` | 否 | `1800` | 失败后最长退避，秒 |
| `MONITOR_TWEET_FETCH_COUNT` | 否 | `10` | 每次拉取最近多少条推文 |
| `MONITOR_FOLLOWING_FETCH_COUNT` | 否 | `40` | 每次拉取最近多少个关注 |
| `MONITOR_INITIAL_FOLLOWING_FETCH_COUNT` | 否 | `200` | 新用户首次基线和手动补齐时拉取多少个关注 |
| `MONITOR_TG_COMMANDS` | 否 | `true` | 是否启用 Telegram Bot 管理 |
| `MONITOR_TIMEZONE` | 否 | `Asia/Shanghai` | 通知时间显示时区 |
| `MONITOR_TRANSLATE_ENABLED` | 否 | `true` | 是否启用翻译 |
| `MONITOR_GOOGLE_TRANSLATE_API_KEY` | 否 | | Google Cloud Translation API Key |
| `MONITOR_TRANSLATE_URL` | 否 | `https://libretranslate.com/translate` | LibreTranslate 接口 |
| `MONITOR_TRANSLATE_API_KEY` | 否 | | LibreTranslate API Key |
| `MONITOR_TRANSLATE_PROXY` | 否 | 通知代理 | 翻译请求代理 |
| `MONITOR_MYMEMORY_ENABLED` | 否 | `true` | LibreTranslate 失败后是否使用 MyMemory |

## 数据和安全

- X Cookie、Telegram Token、WxPusher Token、Bark 设备码不要提交到 GitHub
- `.env`、`*.db`、`*.db-*` 已在 `.gitignore` 中排除
- SQLite 数据库适合个人监控，服务器部署时要放到持久化目录
- Web 管理密码必须通过 `MONITOR_ADMIN_TOKEN` 配置，不要提交到 GitHub
- 只建议把服务绑定到 Tailscale IP 或内网地址

## 开发检查

```bash
uv run ruff check twitter_monitor tests/test_monitor_storage.py tests/test_monitor_poller.py tests/test_monitor_bot.py tests/test_monitor_scheduler.py
uv run pytest tests/test_monitor_storage.py tests/test_monitor_poller.py tests/test_monitor_bot.py tests/test_monitor_scheduler.py -q
uv run mypy twitter_monitor
node --check twitter_monitor/static/app.js
```

## CLI 说明

底层仍保留原 `twitter-cli` 的部分命令能力，主要用于复用 Cookie 登录、X GraphQL 请求、用户和时间线解析能力。当前仓库的主要产品形态是 XMonitor Web 监控平台。
