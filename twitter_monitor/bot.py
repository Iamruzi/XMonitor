"""Telegram command bot for managing monitor settings."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .notifiers import BarkNotifier, WxPusherNotifier
from .settings import MonitorSettings
from .storage import MonitorStorage, normalize_handle

logger = logging.getLogger(__name__)


WATCH_FIELDS = {
    "原创": "monitor_tweets",
    "发推": "monitor_tweets",
    "推文": "monitor_tweets",
    "转推": "monitor_retweets",
    "转发": "monitor_retweets",
    "回复": "monitor_replies",
    "评论": "monitor_replies",
    "关注": "monitor_following",
    "关注变化": "monitor_following",
}
WATCH_LABELS = {
    "monitor_tweets": "原创",
    "monitor_retweets": "转推",
    "monitor_replies": "回复",
    "monitor_following": "关注",
}
ON_WORDS = {"开", "开启", "打开", "启用", "运行", "on", "true", "1", "yes"}
OFF_WORDS = {"关", "关闭", "暂停", "停用", "off", "false", "0", "no"}
BOT_COMMANDS = [
    {"command": "help", "description": "查看命令菜单"},
    {"command": "status", "description": "查看运行状态"},
    {"command": "users", "description": "查看监控用户"},
    {"command": "add", "description": "添加监控用户"},
    {"command": "del", "description": "删除监控用户"},
    {"command": "on", "description": "启用用户监控"},
    {"command": "off", "description": "暂停用户监控"},
    {"command": "watch", "description": "开关原创、转推、回复、关注"},
    {"command": "meta", "description": "设置分组和备注名"},
    {"command": "groups", "description": "查看分组"},
    {"command": "group", "description": "批量改名或清空分组"},
    {"command": "auth", "description": "授权或删除 Telegram 群"},
    {"command": "wxpusher", "description": "管理 WxPusher 通知"},
    {"command": "bark", "description": "管理 Bark 通知"},
]


class TelegramCommandBot:
    def __init__(self, storage: MonitorStorage, settings: MonitorSettings) -> None:
        self.storage = storage
        self.settings = settings
        self.offset: int | None = None
        self._menu_synced = False

    def poll_once(self) -> None:
        token, chat_id, proxy = self._telegram_config()
        if not token or not chat_id:
            return
        self.ensure_menu(token, proxy)
        payload = {
            "timeout": 25,
            "allowed_updates": ["message", "callback_query"],
        }  # type: dict[str, Any]
        if self.offset is not None:
            payload["offset"] = self.offset
        data = self._telegram_request(token, "getUpdates", payload, proxy)
        for update in data.get("result", []):
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self.offset = update_id + 1
            callback_query = update.get("callback_query") or {}
            if callback_query:
                self._handle_callback_query(token, callback_query, chat_id, proxy)
                continue

            message = update.get("message") or {}
            text = str(message.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            chat = message.get("chat") or {}
            source_chat = str(chat.get("id") or "")
            source_title = self._chat_title(chat)
            if not self._chat_authorized(source_chat, chat_id) and not text.lower().startswith("/auth me"):
                self._send_message(
                    token,
                    source_chat,
                    "未授权：当前聊天 ID 是 %s。请让主账号或已授权群执行：/auth add %s %s"
                    % (source_chat, source_chat, source_title),
                    proxy,
                )
                continue
            command = text.split()[0].split("@", 1)[0].lower()
            reply = self.handle_command(text, source_chat_id=source_chat, source_chat_title=source_title)
            if reply:
                reply_markup = self._menu_markup() if command in {"/start", "/help", "/menu", "/菜单"} else None
                self._send_message(token, source_chat, reply, proxy, reply_markup=reply_markup)

    def handle_command(self, text: str, *, source_chat_id: str = "", source_chat_title: str = "") -> str:
        parts = text.split()
        if not parts:
            return ""
        command = parts[0].split("@", 1)[0].lower()
        args = parts[1:]
        try:
            if command in {"/start", "/help", "/menu", "/菜单"}:
                return self._help()
            if command in {"/status", "/状态"}:
                return self._status()
            if command in {"/users", "/用户"}:
                return self._users()
            if command in {"/add", "/添加"}:
                return self._add(args)
            if command in {"/del", "/delete", "/删除"}:
                return self._delete(args)
            if command == "/on":
                return self._set_enabled(args, True)
            if command == "/off":
                return self._set_enabled(args, False)
            if command in {"/watch", "/监控"}:
                return self._watch(args)
            if command in {"/meta", "/备注"}:
                return self._meta(args)
            if command in {"/groups", "/分组"}:
                return self._groups()
            if command in {"/group", "/分组管理"}:
                return self._group(args)
            if command in {"/auth", "/授权"}:
                return self._auth(args, source_chat_id, source_chat_title)
            if command == "/wxpusher":
                return self._wxpusher(args)
            if command == "/bark":
                return self._bark(args)
        except ValueError as exc:
            return "操作失败：%s" % exc
        except Exception:
            logger.exception("Telegram command failed: %s", text)
            return "操作失败：内部错误，请查看服务日志。"
        return "未知命令。发送 /help 查看可用命令。"

    def ensure_menu(self, token: str | None = None, proxy: str | None = None) -> None:
        if self._menu_synced:
            return
        if token is None or proxy is None:
            token, _chat_id, proxy = self._telegram_config()
        if not token:
            return
        self._telegram_request(token, "setMyCommands", {"commands": BOT_COMMANDS}, proxy or "")
        self._telegram_request(token, "setChatMenuButton", {"menu_button": {"type": "commands"}}, proxy or "")
        self._menu_synced = True

    def _help(self) -> str:
        return "\n".join(
            [
                "可用命令：",
                "/status 查看运行状态",
                "/users 查看监控用户",
                "/add @用户名 添加用户，默认监控原创、转推、回复、关注",
                "/add @用户名 原创 关注 只监控指定行为",
                "/del @用户名 删除用户",
                "/on @用户名 启用监控",
                "/off @用户名 暂停监控",
                "/watch @用户名 原创 开",
                "/watch @用户名 转推 关",
                "/meta @用户名 <分组> <备注名> 设置分组和备注名，用 - 清空",
                "/groups 查看当前分组",
                "/group rename <旧分组> <新分组> 批量修改分组名",
                "/group clear <分组> 清空这个分组",
                "/auth me 查看当前聊天 ID",
                "/auth list 查看已授权群",
                "/auth add 当前 授权当前群",
                "/auth add <chat_id> <备注> 授权指定群",
                "/auth del <chat_id> 删除授权",
                "/auth rename <chat_id> <备注> 修改备注",
                "/wxpusher status 查看 WxPusher 配置",
                "/wxpusher token <AppToken> 保存 AppToken",
                "/wxpusher add <UID> 增加接收人",
                "/wxpusher del <UID> 删除接收人",
                "/wxpusher test 发送测试消息",
                "/bark status 查看 Bark 配置",
                "/bark add <设备码> 增加 Bark 设备码",
                "/bark del <设备码> 删除 Bark 设备码",
                "/bark level 普通|时效|紧急 设置通知级别",
                "/bark sound <铃声名> 设置铃声，用 - 清空",
                "/bark call 开|关 设置紧急持续响铃",
                "/bark test 发送 Bark 测试消息",
            ]
        )

    def _status(self) -> str:
        stats = self.storage.stats()
        wx_token, wx_uids, _proxy = self._wxpusher_config()
        bark_server, bark_keys, bark_level, _sound, _group, bark_call, _volume, _proxy = self._bark_config()
        authorized_chats = self.storage.get_telegram_authorized_chats()
        return "\n".join(
            [
                "运行状态：",
                "监控用户：%s 个，运行中：%s 个" % (stats["targets"], stats["enabledTargets"]),
                "发现事件：%s 条，待通知：%s 条" % (stats["events"], stats["pendingNotifications"]),
                "后台轮询间隔：%s 秒" % self.settings.poll_interval_seconds,
                "Telegram 授权群：%s 个" % len(authorized_chats),
                "WxPusher：%s，接收人 %s 个"
                % ("已配置" if wx_token and wx_uids else "未配置", len(wx_uids)),
                "Bark：%s，设备 %s 个，级别 %s%s"
                % (
                    "已配置" if bark_server and bark_keys else "未配置",
                    len(bark_keys),
                    self._bark_level_label(bark_level),
                    "，持续响铃" if bark_call else "",
                ),
            ]
        )

    def _users(self) -> str:
        targets = self.storage.list_targets()
        if not targets:
            return "还没有监控用户。"
        lines = ["监控用户："]
        for target in targets:
            flags = [label for field, label in WATCH_LABELS.items() if target.get(field)]
            status = "运行" if target.get("enabled") else "暂停"
            line = "%s：%s；行为：%s" % (self._target_label(target), status, "、".join(flags) or "无")
            if target.get("last_error"):
                line += "；错误：%s" % str(target["last_error"])[:80]
            lines.append(line)
        return "\n".join(lines)[:3500]

    def _add(self, args: list[str]) -> str:
        if not args:
            raise ValueError("请写用户名，例如 /add @SkyAAmen")
        handle = normalize_handle(args[0])
        flags = self._parse_watch_flags(args[1:])
        target = self.storage.add_target(
            handle,
            monitor_tweets=flags["monitor_tweets"],
            monitor_retweets=flags["monitor_retweets"],
            monitor_replies=flags["monitor_replies"],
            monitor_following=flags["monitor_following"],
        )
        enabled = [label for field, label in WATCH_LABELS.items() if target.get(field)]
        return "已添加 @%s，监控：%s" % (target["handle"], "、".join(enabled) or "无")

    def _delete(self, args: list[str]) -> str:
        target = self._target_from_args(args)
        self.storage.delete_target(int(target["id"]))
        return "已删除 @%s。" % target["handle"]

    def _set_enabled(self, args: list[str], enabled: bool) -> str:
        target = self._target_from_args(args)
        updated = self.storage.update_target(int(target["id"]), {"enabled": enabled}) or target
        return "已%s @%s。" % ("启用" if updated.get("enabled") else "暂停", updated["handle"])

    def _watch(self, args: list[str]) -> str:
        if len(args) < 3:
            raise ValueError("格式：/watch @用户名 原创 开")
        target = self._target_from_args(args)
        watch_name = args[1].strip()
        action = args[2].strip().lower()
        fields = list(WATCH_LABELS.keys()) if watch_name in {"全部", "all"} else [WATCH_FIELDS.get(watch_name, "")]
        fields = [field for field in fields if field]
        if not fields:
            raise ValueError("行为只能是：原创、转推、回复、关注、全部")
        if action in ON_WORDS:
            value = True
        elif action in OFF_WORDS:
            value = False
        else:
            raise ValueError("状态只能是：开 或 关")
        updates = {field: value for field in fields}
        updated = self.storage.update_target(int(target["id"]), updates) or target
        enabled = [label for field, label in WATCH_LABELS.items() if updated.get(field)]
        return "已更新 @%s，当前监控：%s" % (updated["handle"], "、".join(enabled) or "无")

    def _meta(self, args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError("格式：/meta @用户名 <分组> <备注名>，用 - 清空")
        target = self._target_from_args(args)
        group_name = "" if args[1] == "-" else args[1]
        remark_name = ""
        if len(args) >= 3:
            remark_name = " ".join(args[2:])
            if remark_name == "-":
                remark_name = ""
        updated = self.storage.update_target(
            int(target["id"]),
            {"group_name": group_name, "remark_name": remark_name},
        ) or target
        return "已更新：%s。" % self._target_label(updated)

    def _groups(self) -> str:
        targets = self.storage.list_targets()
        counts: dict[str, int] = {}
        for target in targets:
            group_name = str(target.get("group_name") or "").strip()
            if not group_name:
                continue
            counts[group_name] = counts.get(group_name, 0) + 1
        if not counts:
            return "还没有设置分组。用 /meta @用户名 分组 备注名 设置。"
        lines = ["当前分组："]
        for group_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append("- %s：%s 个用户" % (group_name, count))
        lines.extend(
            [
                "",
                "批量改名：/group rename <旧分组> <新分组>",
                "清空分组：/group clear <分组>",
            ]
        )
        return "\n".join(lines)

    def _group(self, args: list[str]) -> str:
        if len(args) < 2:
            raise ValueError("格式：/group rename <旧分组> <新分组> 或 /group clear <分组>")
        action = args[0].lower()
        if action in {"rename", "改名"}:
            if len(args) < 3:
                raise ValueError("格式：/group rename <旧分组> <新分组>")
            old_name = args[1]
            new_name = " ".join(args[2:])
            changed = 0
            for target in self.storage.list_targets():
                if str(target.get("group_name") or "").strip() == old_name:
                    self.storage.update_target(int(target["id"]), {"group_name": new_name})
                    changed += 1
            return "已把分组 %s 改为 %s，影响 %s 个用户。" % (old_name, new_name, changed)
        if action in {"clear", "delete", "del", "删除", "清空"}:
            group_name = " ".join(args[1:])
            changed = 0
            for target in self.storage.list_targets():
                if str(target.get("group_name") or "").strip() == group_name:
                    self.storage.update_target(int(target["id"]), {"group_name": ""})
                    changed += 1
            return "已清空分组 %s，影响 %s 个用户。" % (group_name, changed)
        raise ValueError("未知分组操作。可用：rename、clear")

    def _auth(self, args: list[str], source_chat_id: str, source_chat_title: str) -> str:
        if not args or args[0].lower() in {"list", "列表"}:
            return self._auth_list()
        action = args[0].lower()
        if action == "me":
            return "当前聊天 ID：%s\n当前聊天名称：%s" % (source_chat_id or "未知", source_chat_title or "未命名")
        if action in {"add", "添加"}:
            if len(args) < 2:
                raise ValueError("格式：/auth add 当前 或 /auth add <chat_id> <备注>")
            chat_id = source_chat_id if args[1] in {"当前", "this", "me"} else args[1]
            title = source_chat_title if args[1] in {"当前", "this", "me"} else " ".join(args[2:])
            chats = self.storage.update_telegram_authorized_chats(add_chat_id=chat_id, add_title=title)
            return "已授权 %s，当前授权群 %s 个。" % (self._chat_label(chat_id, title), len(chats))
        if action in {"del", "delete", "remove", "删除"}:
            if len(args) < 2:
                raise ValueError("格式：/auth del <chat_id>")
            chat_id = source_chat_id if args[1] in {"当前", "this", "me"} else args[1]
            chats = self.storage.update_telegram_authorized_chats(remove_chat_id=chat_id)
            return "已删除授权 %s，当前授权群 %s 个。" % (chat_id, len(chats))
        if action in {"rename", "改名", "备注"}:
            if len(args) < 3:
                raise ValueError("格式：/auth rename <chat_id> <备注>")
            chat_id = source_chat_id if args[1] in {"当前", "this", "me"} else args[1]
            title = " ".join(args[2:])
            self.storage.update_telegram_authorized_chats(update_chat_id=chat_id, update_title=title)
            return "已更新授权备注：%s" % self._chat_label(chat_id, title)
        raise ValueError("未知授权操作。可用：list、me、add、del、rename")

    def _auth_list(self) -> str:
        _token, primary_chat_id, _proxy = self._telegram_config()
        lines = ["Telegram 授权："]
        lines.append("主聊天：%s" % (primary_chat_id or "未配置"))
        chats = self.storage.get_telegram_authorized_chats()
        if not chats:
            lines.append("额外授权群：无")
            return "\n".join(lines)
        lines.append("额外授权群：")
        for chat in chats:
            lines.append("- %s" % self._chat_label(chat["id"], chat.get("title", "")))
        return "\n".join(lines)

    def _wxpusher(self, args: list[str]) -> str:
        if not args or args[0].lower() == "status":
            token, uids, _proxy = self._wxpusher_config()
            return "\n".join(
                [
                    "WxPusher 配置：",
                    "AppToken：%s" % self._mask(token),
                    "接收人：%s" % ("、".join(uids) if uids else "未配置"),
                ]
            )
        action = args[0].lower()
        if action == "token":
            if len(args) < 2:
                raise ValueError("格式：/wxpusher token <AppToken>")
            self.storage.update_wxpusher_settings(wxpusher_app_token=args[1])
            return "已保存 WxPusher AppToken。"
        if action == "add":
            if len(args) < 2:
                raise ValueError("格式：/wxpusher add <UID>")
            settings = self.storage.update_wxpusher_settings(wxpusher_add_uid=args[1])
            return "已增加 WxPusher 接收人，当前 %s 个。" % len(settings["wxpusher_uids"])
        if action in {"del", "delete", "remove"}:
            if len(args) < 2:
                raise ValueError("格式：/wxpusher del <UID>")
            settings = self.storage.update_wxpusher_settings(wxpusher_remove_uid=args[1])
            return "已删除 WxPusher 接收人，当前 %s 个。" % len(settings["wxpusher_uids"])
        if action == "test":
            return self._wxpusher_test()
        raise ValueError("未知 WxPusher 操作，发送 /help 查看命令。")

    def _wxpusher_test(self) -> str:
        token, uids, proxy = self._wxpusher_config()
        if not token or not uids:
            raise ValueError("请先配置 AppToken 和 UID")
        result = WxPusherNotifier(token, uids, proxy).send_event(
            {
                "event_type": "test",
                "target_handle": "monitor",
                "title": "WxPusher 测试通知",
                "body": "如果你看到这条消息，说明 WxPusher 通知已经配置成功。",
                "url": "",
            }
        )
        if not result.sent:
            raise ValueError(result.error or "测试发送失败")
        return "WxPusher 测试消息已发送。"

    def _bark(self, args: list[str]) -> str:
        if not args or args[0].lower() == "status":
            server_url, keys, level, sound, group, call, volume, _proxy = self._bark_config()
            return "\n".join(
                [
                    "Bark 配置：",
                    "服务地址：%s" % (server_url or "未配置"),
                    "设备码：%s" % ("、".join(self._mask(key) for key in keys) if keys else "未配置"),
                    "通知级别：%s" % self._bark_level_label(level),
                    "紧急持续响铃：%s" % ("开启" if call else "关闭"),
                    "响铃音量：%s" % volume,
                    "铃声：%s" % (sound or "默认"),
                    "推送分组：%s" % (group or "XMonitor"),
                ]
            )
        action = args[0].lower()
        if action == "server":
            if len(args) < 2:
                raise ValueError("格式：/bark server https://api.day.app")
            self.storage.update_bark_settings(bark_server_url=args[1])
            return "已保存 Bark 服务地址。"
        if action in {"add", "key"}:
            if len(args) < 2:
                raise ValueError("格式：/bark add <设备码>")
            settings = self.storage.update_bark_settings(bark_add_device_key=args[1])
            return "已增加 Bark 设备码，当前 %s 个。" % len(settings["bark_device_keys"])
        if action in {"del", "delete", "remove"}:
            if len(args) < 2:
                raise ValueError("格式：/bark del <设备码>")
            settings = self.storage.update_bark_settings(bark_remove_device_key=args[1])
            return "已删除 Bark 设备码，当前 %s 个。" % len(settings["bark_device_keys"])
        if action == "level":
            if len(args) < 2:
                raise ValueError("格式：/bark level 普通|时效|紧急")
            level = self._normalize_bark_level(args[1])
            self.storage.update_bark_settings(bark_level=level)
            return "已设置 Bark 通知级别：%s。" % self._bark_level_label(level)
        if action == "sound":
            if len(args) < 2:
                raise ValueError("格式：/bark sound <铃声名>，用 - 清空")
            sound = "" if args[1] in {"-", "默认", "none", "default"} else args[1]
            self.storage.update_bark_settings(bark_sound=sound)
            return "已设置 Bark 铃声：%s。" % (sound or "默认")
        if action == "group":
            if len(args) < 2:
                raise ValueError("格式：/bark group <推送分组>")
            group = " ".join(args[1:])
            self.storage.update_bark_settings(bark_group=group)
            return "已设置 Bark 推送分组：%s。" % group
        if action == "call":
            if len(args) < 2:
                raise ValueError("格式：/bark call 开|关")
            value = self._parse_switch(args[1])
            self.storage.update_bark_settings(bark_call=value)
            return "已%s Bark 紧急持续响铃。" % ("开启" if value else "关闭")
        if action == "volume":
            if len(args) < 2:
                raise ValueError("格式：/bark volume 0-10")
            volume = min(max(int(args[1]), 0), 10)
            self.storage.update_bark_settings(bark_volume=volume)
            return "已设置 Bark 紧急音量：%s。" % volume
        if action == "test":
            return self._bark_test()
        raise ValueError("未知 Bark 操作，发送 /help 查看命令。")

    def _bark_test(self) -> str:
        server_url, keys, level, sound, group, call, volume, proxy = self._bark_config()
        if not server_url or not keys:
            raise ValueError("请先配置 Bark 服务地址和设备码")
        result = BarkNotifier(
            server_url,
            keys,
            level=level,
            sound=sound,
            group=group,
            call=call,
            volume=volume,
            proxy=proxy,
        ).send_event(
            {
                "event_type": "test",
                "target_handle": "monitor",
                "title": "Bark 测试通知",
                "body": "如果你看到这条消息，说明 Bark 通知已经配置成功。",
                "url": "",
            }
        )
        if not result.sent:
            raise ValueError(result.error or "测试发送失败")
        return "Bark 测试消息已发送。"

    def _parse_watch_flags(self, names: list[str]) -> dict[str, bool]:
        flags = {
            "monitor_tweets": True,
            "monitor_retweets": True,
            "monitor_replies": True,
            "monitor_following": True,
        }
        if not names:
            return flags
        flags = {field: False for field in flags}
        for name in names:
            if name in {"全部", "all"}:
                return {field: True for field in flags}
            field = WATCH_FIELDS.get(name.strip())
            if not field:
                raise ValueError("行为只能是：原创、转推、回复、关注、全部")
            flags[field] = True
        return flags

    def _target_from_args(self, args: list[str]) -> dict[str, Any]:
        if not args:
            raise ValueError("请写用户名")
        handle = normalize_handle(args[0])
        target = self.storage.get_target_by_handle(handle)
        if not target:
            raise ValueError("没有找到监控用户 @%s" % handle)
        return target

    def _telegram_config(self) -> tuple[str, str, str]:
        db_settings = self.storage.get_notification_settings()
        token = db_settings.get("telegram_bot_token") or self.settings.telegram_bot_token
        chat_id = db_settings.get("telegram_chat_id") or self.settings.telegram_chat_id
        proxy = db_settings.get("telegram_proxy") or self.settings.telegram_proxy
        return token, chat_id, proxy

    def _chat_authorized(self, source_chat_id: str, primary_chat_id: str) -> bool:
        if not source_chat_id:
            return False
        if str(source_chat_id) == str(primary_chat_id):
            return True
        return source_chat_id in self.storage.get_telegram_authorized_chat_ids()

    def _chat_title(self, chat: dict[str, Any]) -> str:
        for key in ("title", "username", "first_name"):
            value = str(chat.get(key) or "").strip()
            if value:
                return value
        return ""

    def _chat_label(self, chat_id: str, title: str = "") -> str:
        return "%s（%s）" % (chat_id, title) if title else chat_id

    def _target_label(self, target: dict[str, Any]) -> str:
        group_name = str(target.get("group_name") or "").strip()
        remark_name = str(target.get("remark_name") or "").strip()
        display_name = str(target.get("display_name") or "").strip()
        handle = str(target.get("handle") or "").strip()
        profile = "%s（@%s）" % (display_name, handle) if display_name and display_name != handle else "@%s" % handle
        parts = []
        if group_name:
            parts.append("【%s】" % group_name)
        if remark_name:
            parts.append(remark_name)
        parts.append(profile)
        return " ".join(parts)

    def _wxpusher_config(self) -> tuple[str, list[str], str]:
        wx_settings = self.storage.get_wxpusher_settings()
        token = wx_settings.get("wxpusher_app_token") or self.settings.wxpusher_app_token
        uids = wx_settings.get("wxpusher_uids") or self._split_uids(self.settings.wxpusher_uids)
        proxy = self.storage.get_app_setting("telegram_proxy") or self.settings.telegram_proxy
        return str(token or ""), [str(uid) for uid in uids], proxy

    def _bark_config(self) -> tuple[str, list[str], str, str, str, bool, int, str]:
        bark_settings = self.storage.get_bark_settings()
        server_url = bark_settings.get("bark_server_url") or self.settings.bark_server_url
        keys = bark_settings.get("bark_device_keys") or self._split_uids(self.settings.bark_device_keys)
        level = bark_settings.get("bark_level") or self.settings.bark_level
        sound = bark_settings.get("bark_sound") or self.settings.bark_sound
        group = bark_settings.get("bark_group") or self.settings.bark_group
        call = self._setting_bool(bark_settings.get("bark_call"), self.settings.bark_call)
        volume = min(max(self._setting_int(bark_settings.get("bark_volume"), self.settings.bark_volume), 0), 10)
        proxy = self.storage.get_app_setting("telegram_proxy") or self.settings.telegram_proxy
        return (
            str(server_url or ""),
            [str(key) for key in keys],
            self._normalize_bark_level(str(level or "")),
            str(sound or ""),
            str(group or "XMonitor"),
            call,
            volume,
            proxy,
        )

    def _split_uids(self, raw: str) -> list[str]:
        return [uid.strip() for uid in raw.replace(";", ",").split(",") if uid.strip()]

    def _parse_switch(self, value: str) -> bool:
        normalized = value.strip().lower()
        if normalized in ON_WORDS:
            return True
        if normalized in OFF_WORDS:
            return False
        raise ValueError("状态只能是：开 或 关")

    def _setting_bool(self, raw: Any, default: bool) -> bool:
        if raw in (None, ""):
            return default
        return str(raw).strip().lower() in ON_WORDS

    def _setting_int(self, raw: Any, default: int) -> int:
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return default

    def _normalize_bark_level(self, value: str) -> str:
        normalized = str(value or "").strip()
        aliases = {
            "被动": "passive",
            "普通": "active",
            "默认": "active",
            "及时": "timeSensitive",
            "时效": "timeSensitive",
            "紧急": "critical",
            "critical": "critical",
            "timesensitive": "timeSensitive",
            "timeSensitive": "timeSensitive",
            "active": "active",
            "passive": "passive",
        }
        return aliases.get(normalized, aliases.get(normalized.lower(), "active"))

    def _bark_level_label(self, level: str) -> str:
        labels = {
            "passive": "被动",
            "active": "普通",
            "timeSensitive": "时效",
            "critical": "紧急",
        }
        return labels.get(self._normalize_bark_level(level), "普通")

    def _telegram_request(self, token: str, method: str, payload: dict[str, Any], proxy: str) -> dict[str, Any]:
        request = urllib.request.Request(
            "https://api.telegram.org/bot%s/%s" % (token, method),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = self._opener(proxy)
        try:
            with opener.open(request, timeout=35) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError("telegram_http_%d: %s" % (exc.code, detail)) from exc
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}

    def _handle_callback_query(self, token: str, callback_query: dict[str, Any], primary_chat_id: str, proxy: str) -> None:
        callback_id = str(callback_query.get("id") or "")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        source_chat = str(chat.get("id") or "")
        source_title = self._chat_title(chat)
        if callback_id:
            self._telegram_request(token, "answerCallbackQuery", {"callback_query_id": callback_id}, proxy)
        if not self._chat_authorized(source_chat, primary_chat_id):
            self._send_message(
                token,
                source_chat,
                "未授权：当前聊天 ID 是 %s。请先让主账号或已授权群执行 /auth add。" % source_chat,
                proxy,
            )
            return
        text, reply_markup = self._callback_reply(str(callback_query.get("data") or ""), source_chat, source_title)
        message_id = message.get("message_id")
        if message_id:
            self._telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": source_chat,
                    "message_id": message_id,
                    "text": text,
                    "reply_markup": reply_markup,
                    "disable_web_page_preview": True,
                },
                proxy,
            )
        else:
            self._send_message(token, source_chat, text, proxy, reply_markup=reply_markup)

    def _callback_reply(self, data: str, source_chat_id: str, source_chat_title: str) -> tuple[str, dict[str, Any]]:
        if data == "menu:status":
            return self._status(), self._menu_markup()
        if data == "menu:users":
            return self._users(), self._menu_markup()
        if data == "menu:add":
            return self._add_guide(), self._menu_markup()
        if data == "menu:watch":
            return self._watch_guide(), self._menu_markup()
        if data == "menu:meta":
            return self._meta_guide(), self._menu_markup()
        if data == "menu:groups":
            return self._groups(), self._menu_markup()
        if data == "menu:auth":
            return self._auth_guide(source_chat_id, source_chat_title), self._menu_markup()
        if data == "menu:wxpusher":
            return self._wxpusher_guide(), self._menu_markup()
        if data == "menu:bark":
            return self._bark_guide(), self._menu_markup()
        return self._help(), self._menu_markup()

    def _menu_markup(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "运行状态", "callback_data": "menu:status"},
                    {"text": "监控用户", "callback_data": "menu:users"},
                ],
                [
                    {"text": "添加用户", "callback_data": "menu:add"},
                    {"text": "行为开关", "callback_data": "menu:watch"},
                ],
                [
                    {"text": "分组备注", "callback_data": "menu:meta"},
                    {"text": "分组列表", "callback_data": "menu:groups"},
                ],
                [
                    {"text": "授权群", "callback_data": "menu:auth"},
                    {"text": "WxPusher", "callback_data": "menu:wxpusher"},
                    {"text": "Bark", "callback_data": "menu:bark"},
                ],
                [{"text": "返回菜单", "callback_data": "menu:home"}],
            ]
        }

    def _add_guide(self) -> str:
        return "\n".join(
            [
                "添加监控用户：",
                "/add @用户名",
                "/add @用户名 原创 关注",
                "",
                "添加后可以继续设置分组和备注：",
                "/meta @用户名 alpha猎手 wx好友流星",
            ]
        )

    def _watch_guide(self) -> str:
        return "\n".join(
            [
                "开关监控行为：",
                "/watch @用户名 原创 开",
                "/watch @用户名 转推 关",
                "/watch @用户名 回复 开",
                "/watch @用户名 关注 开",
                "/watch @用户名 全部 开",
            ]
        )

    def _meta_guide(self) -> str:
        return "\n".join(
            [
                "分组和备注名：",
                "/meta @用户名 <分组> <备注名>",
                "",
                "例子：",
                "/meta @0xliuxing alpha猎手 wx好友流星",
                "",
                "清空分组或备注名可以写 -：",
                "/meta @0xliuxing - -",
                "",
                "通知标题会显示：alpha猎手｜wx好友流星｜流星（@0xliuxing）",
            ]
        )

    def _auth_guide(self, source_chat_id: str, source_chat_title: str) -> str:
        return "\n".join(
            [
                "Telegram 群授权：",
                "当前聊天 ID：%s" % (source_chat_id or "未知"),
                "当前聊天名称：%s" % (source_chat_title or "未命名"),
                "",
                "/auth add 当前",
                "/auth list",
                "/auth del 当前",
            ]
        )

    def _wxpusher_guide(self) -> str:
        return "\n".join(
            [
                "WxPusher 管理：",
                "/wxpusher status",
                "/wxpusher token <AppToken>",
                "/wxpusher add <UID>",
                "/wxpusher del <UID>",
                "/wxpusher test",
            ]
        )

    def _bark_guide(self) -> str:
        return "\n".join(
            [
                "Bark 管理：",
                "/bark status",
                "/bark server https://api.day.app",
                "/bark add <设备码>",
                "/bark del <设备码>",
                "/bark level 普通",
                "/bark level 时效",
                "/bark level 紧急",
                "/bark call 开",
                "/bark sound minuet",
                "/bark sound -",
                "/bark volume 8",
                "/bark test",
            ]
        )

    def _send_message(
        self,
        token: str,
        chat_id: str,
        text: str,
        proxy: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if not chat_id:
            return
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}  # type: dict[str, Any]
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._telegram_request(
            token,
            "sendMessage",
            payload,
            proxy,
        )

    def _opener(self, proxy: str) -> urllib.request.OpenerDirector:
        if not proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )

    def _mask(self, value: str) -> str:
        if not value:
            return "未配置"
        if len(value) <= 8:
            return "已保存"
        return "%s...%s" % (value[:4], value[-4:])
