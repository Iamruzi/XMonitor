from __future__ import annotations

from twitter_monitor.bot import TelegramCommandBot
from twitter_monitor.settings import MonitorSettings
from twitter_monitor.storage import MonitorStorage


def _settings(db_path: str) -> MonitorSettings:
    return MonitorSettings(
        db_path=db_path,
        poll_interval_seconds=60,
        poll_interval_min_seconds=180,
        poll_interval_max_seconds=300,
        poll_backoff_max_seconds=1800,
        background_worker=False,
        default_tweet_fetch_count=10,
        default_following_fetch_count=10,
        admin_token="",
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_proxy="",
        wxpusher_app_token="",
        wxpusher_uids="",
        telegram_commands_enabled=True,
    )


def test_telegram_bot_manages_targets_and_watch_flags(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    bot = TelegramCommandBot(storage, _settings(storage.db_path))

    reply = bot.handle_command("/add @alice 原创 关注")
    target = storage.get_target_by_handle("alice")

    assert "已添加 @alice" in reply
    assert target is not None
    assert target["monitor_tweets"] is True
    assert target["monitor_retweets"] is False
    assert target["monitor_replies"] is False
    assert target["monitor_following"] is True

    reply = bot.handle_command("/watch @alice 转推 开")
    target = storage.get_target_by_handle("alice")

    assert "已更新 @alice" in reply
    assert target is not None
    assert target["monitor_retweets"] is True

    reply = bot.handle_command("/meta @alice alpha猎手 wx好友流星")
    target = storage.get_target_by_handle("alice")

    assert "alpha猎手" in reply
    assert "wx好友流星" in reply
    assert target is not None
    assert target["group_name"] == "alpha猎手"
    assert target["remark_name"] == "wx好友流星"
    assert "alpha猎手：1 个用户" in bot.handle_command("/groups")
    assert "影响 1 个用户" in bot.handle_command("/group rename alpha猎手 beta观察")
    target = storage.get_target_by_handle("alice")
    assert target is not None
    assert target["group_name"] == "beta观察"

    reply = bot.handle_command("/off @alice")
    target = storage.get_target_by_handle("alice")

    assert "已暂停 @alice" in reply
    assert target is not None
    assert target["enabled"] is False

    reply = bot.handle_command("/del @alice")

    assert "已删除 @alice" in reply
    assert storage.get_target_by_handle("alice") is None


def test_telegram_bot_manages_wxpusher_settings(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    bot = TelegramCommandBot(storage, _settings(storage.db_path))

    assert bot.handle_command("/wxpusher token app-token") == "已保存 WxPusher AppToken。"
    assert "当前 1 个" in bot.handle_command("/wxpusher add UID_a")
    assert "当前 2 个" in bot.handle_command("/wxpusher add UID_b")
    assert "当前 1 个" in bot.handle_command("/wxpusher del UID_a")

    settings = storage.get_wxpusher_settings()
    status = bot.handle_command("/wxpusher status")

    assert settings["wxpusher_app_token"] == "app-token"
    assert settings["wxpusher_uids"] == ["UID_b"]
    assert "app-...oken" in status
    assert "UID_b" in status


def test_telegram_bot_manages_bark_settings(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    bot = TelegramCommandBot(storage, _settings(storage.db_path))

    assert bot.handle_command("/bark server https://api.day.app") == "已保存 Bark 服务地址。"
    assert "当前 1 个" in bot.handle_command("/bark add device-key-a")
    assert "当前 2 个" in bot.handle_command("/bark add device-key-b")
    assert "紧急" in bot.handle_command("/bark level 紧急")
    assert "已开启" in bot.handle_command("/bark call 开")
    assert "minuet" in bot.handle_command("/bark sound minuet")
    assert "8" in bot.handle_command("/bark volume 8")
    assert "当前 1 个" in bot.handle_command("/bark del device-key-a")

    settings = storage.get_bark_settings()
    status = bot.handle_command("/bark status")

    assert settings["bark_server_url"] == "https://api.day.app"
    assert settings["bark_device_keys"] == ["device-key-b"]
    assert settings["bark_level"] == "critical"
    assert settings["bark_call"] == "1"
    assert settings["bark_sound"] == "minuet"
    assert settings["bark_volume"] == "8"
    assert "devi...ey-b" in status
    assert "紧急" in status


def test_telegram_bot_manages_authorized_chats(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    bot = TelegramCommandBot(storage, _settings(storage.db_path))

    assert "当前聊天 ID：-1001" in bot.handle_command(
        "/auth me",
        source_chat_id="-1001",
        source_chat_title="测试群",
    )
    assert "已授权 -1001（测试群）" in bot.handle_command(
        "/auth add 当前",
        source_chat_id="-1001",
        source_chat_title="测试群",
    )
    assert bot._chat_authorized("-1001", "owner") is True

    reply = bot.handle_command("/auth list")
    assert "主聊天：未配置" in reply
    assert "-1001（测试群）" in reply

    assert "已更新授权备注" in bot.handle_command("/auth rename -1001 核心群")
    assert "核心群" in bot.handle_command("/auth list")

    assert "已删除授权 -1001" in bot.handle_command("/auth del -1001")
    assert bot._chat_authorized("-1001", "owner") is False


def test_telegram_bot_registers_command_menu(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    settings = _settings(storage.db_path)
    settings = MonitorSettings(
        db_path=settings.db_path,
        poll_interval_seconds=settings.poll_interval_seconds,
        poll_interval_min_seconds=settings.poll_interval_min_seconds,
        poll_interval_max_seconds=settings.poll_interval_max_seconds,
        poll_backoff_max_seconds=settings.poll_backoff_max_seconds,
        background_worker=settings.background_worker,
        default_tweet_fetch_count=settings.default_tweet_fetch_count,
        default_following_fetch_count=settings.default_following_fetch_count,
        admin_token=settings.admin_token,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
        telegram_proxy="",
        wxpusher_app_token=settings.wxpusher_app_token,
        wxpusher_uids=settings.wxpusher_uids,
        telegram_commands_enabled=settings.telegram_commands_enabled,
    )
    bot = TelegramCommandBot(storage, settings)
    calls = []

    def fake_request(token, method, payload, proxy):
        calls.append((token, method, payload, proxy))
        return {"ok": True}

    bot._telegram_request = fake_request  # type: ignore[method-assign]

    bot.ensure_menu()
    bot.ensure_menu()

    methods = [call[1] for call in calls]
    assert methods == ["setMyCommands", "setChatMenuButton"]
    assert calls[0][2]["commands"][0]["command"] == "help"
    assert {"command": "auth", "description": "授权或删除 Telegram 群"} in calls[0][2]["commands"]
    assert {"command": "meta", "description": "设置分组和备注名"} in calls[0][2]["commands"]
    assert calls[1][2]["menu_button"]["type"] == "commands"


def test_telegram_bot_guided_menu_callbacks(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    bot = TelegramCommandBot(storage, _settings(storage.db_path))

    text, markup = bot._callback_reply("menu:meta", "-1001", "测试群")

    assert "/meta @用户名 <分组> <备注名>" in text
    assert "inline_keyboard" in markup
    assert any(button["callback_data"] == "menu:groups" for row in markup["inline_keyboard"] for button in row)
    assert any(button["callback_data"] == "menu:bark" for row in markup["inline_keyboard"] for button in row)
