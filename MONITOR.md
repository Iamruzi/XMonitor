# X 用户监控台

这是基于 `twitter-cli` 做的轻量监控平台。它复用现有的 X/Twitter Cookie 登录能力，
提供一个中文 Web 控制台，用来管理监控用户、监控状态、Telegram 通知和 WxPusher 通知。

## 能监控什么

- 原创发推：用户自己发布的新推文
- 转推：用户转发别人的推文
- 回复：用户回复别人的推文
- 关注变化：用户新增关注了谁
- 外文内容翻译：通知里会尝试把非中文简介或正文翻译成中文
- 分组和备注：每个监控用户可以设置分组和备注名，通知标题会优先显示“分组｜备注名｜用户名（@handle）”

第一次检查只建立基线，不会把历史内容当成新事件。后续检查发现新增内容后，会写入事件列表，并向已经配置的通知渠道发送消息。

## 本地运行

```powershell
cd D:\Works\Deng\twitter-cli

$env:PYTHONUTF8="1"
$env:TWITTER_AUTH_TOKEN="你的 auth_token"
$env:TWITTER_CT0="你的 ct0"
$env:TWITTER_PROXY="http://127.0.0.1:7897"

# 可选：修改控制台管理密码；默认是 Vip.123456
$env:MONITOR_ADMIN_TOKEN="change-me"

uv sync
uv run twitter-monitor
```

打开：

```text
http://127.0.0.1:8000
```

打开页面后会先进入登录页。默认管理密码是 `Vip.123456`。部署到公网后建议设置自己的 `MONITOR_ADMIN_TOKEN`。

## 管理密码有什么用

管理密码用于保护 Web 控制台和所有会修改数据的接口，包括添加/删除监控用户、修改监控行为、
保存 Telegram/WxPusher 配置、立即检查和查看受保护的数据列表。前端登录后会把密码保存在浏览器本地，
后续请求会通过 `X-Admin-Token` 请求头带给后端。

默认密码是 `Vip.123456`，只是为了本地启动后可以直接使用。只要准备放到公网、免费托管平台或共享网络，
都建议通过环境变量改掉：

```powershell
$env:MONITOR_ADMIN_TOKEN="换成你自己的强密码"
```

## 通知渠道

可以直接在前端页面里配置 Telegram 和 WxPusher。

## 分组和备注名

监控用户支持两个内部字段：

- 分组：用来表示来源或圈层，例如 `alpha猎手`
- 备注名：你给这个监控用户起的名字，例如 `wx好友流星`

用户的 X 显示名和用户名仍会保留。通知会按下面的方式组织来源：

```text
alpha猎手｜wx好友流星｜流星（@0xliuxing） 于 2026-05-25 12:00:00 关注了 xxx
```

前端可以在添加用户时填写分组和备注名，也可以在监控用户表格里逐行编辑。事件中心支持按分组和用户筛选。
右上角“设置”里的“分组管理”可以新增空分组、集中查看已有分组、批量改名、清空某个分组，清空分组不会删除用户。

WxPusher 的标题摘要会包含分组、备注名和用户名，手机通知列表里可以直接看出是哪一组、哪个人触发的事件。

## 批量导入和导出

右上角“设置”里可以批量导入监控用户，也可以导出当前监控用户 CSV。

导入支持每行一个用户：

```text
@0xliuxing,alpha猎手,wx好友流星
SkyAAmen,项目方,重点观察
```

导出的 CSV 可以修改后再导入。默认情况下，如果导入的用户已经存在，会更新它的分组、备注名和监控行为，不会重复创建。

Telegram 需要：

- Bot Token
- 接收消息的 Chat ID
- 通知代理

WxPusher 需要：

- AppToken
- 接收人 UID，多个 UID 用逗号分隔

WxPusher 消息使用 HTML 格式，通知里的 X 用户名、关注对象、正文里的 `@用户名` 和普通链接都会尽量转成可点击链接。

Token 会保存到本地 SQLite，不会在接口和页面里回显完整明文。

也可以用环境变量提前配置：

```powershell
$env:TELEGRAM_BOT_TOKEN="123456:bot-token"
$env:TELEGRAM_CHAT_ID="123456789"
$env:TELEGRAM_PROXY="http://127.0.0.1:7897"
$env:WXPUSHER_APP_TOKEN="AT_xxx"
$env:WXPUSHER_UIDS="UID_xxx,UID_yyy"
```

如果没有设置 `TELEGRAM_PROXY`，系统会默认复用 `TWITTER_PROXY`。

## Telegram Bot 管理命令

只要配置了 Telegram Bot Token 和 Chat ID，服务会自动启动 Telegram 长轮询管理器。
管理命令只接受配置里的 Chat ID，其他聊天发来的命令会被拒绝。

常用命令：

```text
/help                       查看菜单
/status                     查看运行状态
/users                      查看监控用户
/add @用户名                添加用户，默认监控原创、转推、回复、关注
/add @用户名 原创 关注      添加用户，只监控指定行为
/del @用户名                删除用户
/on @用户名                 启用这个用户的监控
/off @用户名                暂停这个用户的监控
/watch @用户名 原创 开      打开某个行为
/watch @用户名 转推 关      关闭某个行为
/watch @用户名 全部 开      打开全部行为
/meta @用户名 分组 备注名    设置这个用户的分组和备注名
/meta @用户名 - -           清空这个用户的分组和备注名
/groups                    查看已有分组
/group rename 旧分组 新分组 批量修改分组名
/group clear 分组           清空某个分组
/auth me                   查看当前聊天 ID
/auth list                 查看已授权群
/auth add 当前             授权当前群，并让它接收通知
/auth add <chat_id> <备注> 授权指定群
/auth del <chat_id>        删除授权
/auth rename <chat_id> <备注> 修改授权备注
/wxpusher status            查看 WxPusher 配置
/wxpusher token <AppToken>  保存 WxPusher AppToken
/wxpusher add <UID>         增加 WxPusher 接收人
/wxpusher del <UID>         删除 WxPusher 接收人
/wxpusher test              发送 WxPusher 测试消息
```

默认只有“接收消息的聊天 ID”有管理权限。把 Bot 拉到新群以后，先在新群里发：

发送 `/start`、`/help` 或 `/menu` 会出现带按钮的文字引导菜单，里面包含添加用户、行为开关、
分组备注、群授权和 WxPusher 的操作说明。Bot 同时保留 Telegram 原生命令菜单。

```text
/auth me
```

Bot 会返回当前群的 Chat ID。然后在主聊天或任意已授权群里执行：

```text
/auth add <chat_id> <备注>
```

授权后，这个群可以执行管理命令，也会收到 Telegram 监控通知。删除授权后，该群不再能管理，也不再收到 Telegram 通知。

如果不想启动 Telegram 管理器，可以设置：

```powershell
$env:MONITOR_TG_COMMANDS="false"
```

## 关键环境变量

| 名称 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `TWITTER_AUTH_TOKEN` | 是 | | X/Twitter 的 `auth_token` Cookie |
| `TWITTER_CT0` | 是 | | X/Twitter 的 `ct0` Cookie |
| `TWITTER_PROXY` | 否 | | 访问 X/Twitter 的代理 |
| `TELEGRAM_BOT_TOKEN` | 否 | | Telegram Bot Token，也可在页面配置 |
| `TELEGRAM_CHAT_ID` | 否 | | 接收 Telegram 消息的 Chat ID，也可在页面配置 |
| `TELEGRAM_PROXY` | 否 | `TWITTER_PROXY` | Telegram 通知代理 |
| `WXPUSHER_APP_TOKEN` | 否 | | WxPusher AppToken，也可在页面或 TG Bot 配置 |
| `WXPUSHER_UIDS` | 否 | | WxPusher 接收人 UID，多个用逗号分隔 |
| `MONITOR_TG_COMMANDS` | 否 | `true` | 是否启用 Telegram Bot 管理命令 |
| `MONITOR_ADMIN_TOKEN` | 否 | `Vip.123456` | 管理控制台和受保护 API 的密码 |
| `MONITOR_DB_PATH` | 否 | `twitter-monitor.db` | SQLite 数据库路径 |
| `MONITOR_POLL_INTERVAL_MIN` | 否 | `180` | 后台检查最短等待，单位秒 |
| `MONITOR_POLL_INTERVAL_MAX` | 否 | `300` | 后台检查最长等待，单位秒 |
| `MONITOR_POLL_BACKOFF_MAX` | 否 | `1800` | 检查失败后的最长退避等待，单位秒 |
| `MONITOR_POLL_INTERVAL` | 否 | | 旧版固定间隔变量；未设置区间变量时会兼容使用 |
| `MONITOR_BACKGROUND_WORKER` | 否 | `true` | 是否启用内置后台检查 |
| `MONITOR_TWEET_FETCH_COUNT` | 否 | `10` | 每次检查最近多少条推文 |
| `MONITOR_FOLLOWING_FETCH_COUNT` | 否 | `40` | 每次检查最近多少个关注 |
| `MONITOR_TIMEZONE` | 否 | `Asia/Shanghai` | 通知里显示时间使用的时区 |
| `MONITOR_TRANSLATE_ENABLED` | 否 | `true` | 是否尝试翻译外文内容 |
| `MONITOR_GOOGLE_TRANSLATE_API_KEY` | 否 | | Google Cloud Translation API Key，配置后优先使用 |
| `MONITOR_GOOGLE_TRANSLATE_TARGET` | 否 | `zh-CN` | Google 翻译目标语言 |
| `MONITOR_TRANSLATE_URL` | 否 | `https://libretranslate.com/translate` | LibreTranslate 接口地址 |
| `MONITOR_TRANSLATE_API_KEY` | 否 | | LibreTranslate API Key，公共实例可能需要 |
| `MONITOR_TRANSLATE_PROXY` | 否 | 通知代理 | 翻译请求代理 |
| `MONITOR_MYMEMORY_ENABLED` | 否 | `true` | LibreTranslate 失败后是否使用 MyMemory 兜底 |
| `MONITOR_MYMEMORY_SOURCE` | 否 | `en` | MyMemory 源语言 |
| `MONITOR_MYMEMORY_TARGET` | 否 | `zh-CN` | MyMemory 目标语言 |
| `MONITOR_MYMEMORY_EMAIL` | 否 | | MyMemory 的 `de` 参数，可提高免费额度 |

## 外文翻译

如果配置了 `MONITOR_GOOGLE_TRANSLATE_API_KEY`，系统会优先使用 Google Cloud Translation。
Google 官方接口需要 API key，但有每月免费额度。没有 Google key 时，系统会使用 LibreTranslate；
如果 LibreTranslate 失败，会自动用 MyMemory 作为兜底翻译源。MyMemory 默认按英文到简体中文翻译。

公共翻译实例可能限流或要求 API Key。最稳定的免费方案是自建 LibreTranslate，然后把
`MONITOR_TRANSLATE_URL` 指向自己的服务：

```powershell
$env:MONITOR_TRANSLATE_URL="http://127.0.0.1:5000/translate"
```

翻译失败不会阻塞事件记录和 Telegram 通知。

## 免费托管平台

这个服务是普通 ASGI Web 应用，读取平台提供的 `PORT` 环境变量。可以部署到支持 Python 或 Docker 的平台。

免费平台常见问题是服务休眠。休眠平台建议：

```text
MONITOR_BACKGROUND_WORKER=false
```

然后用平台自带 cron 或外部 cron 定时请求：

```text
POST /api/poll/run
```

如果设置了 `MONITOR_ADMIN_TOKEN`，请求头需要带：

```text
X-Admin-Token: change-me
```

这里的值要换成你的管理密码；如果还没改过，就是默认的 `Vip.123456`。

SQLite 适合个人监控。部署时要把 `MONITOR_DB_PATH` 放到持久化磁盘路径，否则重启或重新部署后基线和事件会丢失。

后台检查默认不是固定频率，而是在 `MONITOR_POLL_INTERVAL_MIN` 到 `MONITOR_POLL_INTERVAL_MAX`
之间随机等待。只要本轮有监控用户检查失败，下一轮会自动退避：默认约 `300-600` 秒、`600-1200` 秒，
最多到 `MONITOR_POLL_BACKOFF_MAX`。下一轮成功后会恢复普通随机区间。

这些检查参数也可以直接在前端“设置”里的“后台检查”中修改。页面保存的值会写入 SQLite，并覆盖环境变量默认值；
保存后会影响下一轮后台检查，当前已经在等待的一轮不会被强制打断。

Telegram Bot 管理器使用长轮询，适合本地或单实例免费托管。不要同时运行多个实例，否则同一条命令可能被多个实例处理。
如果部署平台支持固定公网域名，后续可以把它改成 webhook。

## 常用 API

```text
GET  /api/config
GET  /api/targets
POST /api/targets
PATCH /api/targets/{id}
DELETE /api/targets/{id}
POST /api/targets/{id}/poll
POST /api/poll/run
POST /api/users/resolve
GET  /api/groups
POST /api/groups
POST /api/groups/rename
POST /api/groups/clear
POST /api/poll-settings
POST /api/targets/import
GET  /api/events
GET  /api/notification-settings
POST /api/notification-settings
POST /api/notification-settings/test
```
