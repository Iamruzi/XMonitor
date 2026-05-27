from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from twitter_cli.models import Author, Metrics, Tweet, UserProfile
from twitter_monitor.notifiers import BarkNotifier, EventFormatter, TelegramNotifier, WxPusherNotifier
from twitter_monitor.notifiers import NotificationResult
from twitter_monitor.poller import MonitorPoller
from twitter_monitor.settings import MonitorSettings
from twitter_monitor.storage import MonitorStorage


@dataclass
class FakeNotifier:
    sent: int = 0

    @property
    def configured(self) -> bool:
        return True

    def send_event(self, event):
        self.sent += 1
        return NotificationResult(True)


class FakeClient:
    def __init__(self) -> None:
        self.tweets = [
            Tweet(
                id="t1",
                text="first",
                author=Author(id="u1", name="Alice", screen_name="alice"),
                metrics=Metrics(),
                created_at="Fri May 22 00:00:00 +0000 2026",
            )
        ]
        self.following = [UserProfile(id="f1", name="Bob", screen_name="bob")]

    def fetch_user(self, handle: str) -> UserProfile:
        return UserProfile(id="target-id", name="Target", screen_name=handle)

    def fetch_user_tweets(self, user_id: str, count: int):
        return self.tweets[:count]

    def fetch_following(self, user_id: str, count: int):
        return self.following[:count]


class FakePoller(MonitorPoller):
    def __init__(self, storage, settings, notifier, client):
        super().__init__(storage, settings, notifier)
        self.client = client

    def _make_client(self):
        return self.client


def _settings(db_path: str) -> MonitorSettings:
    return MonitorSettings(
        db_path=db_path,
        poll_interval_seconds=300,
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


def test_first_poll_snapshots_without_notifications(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("alice")
    notifier = FakeNotifier()
    poller = FakePoller(storage, _settings(storage.db_path), notifier, FakeClient())

    result = poller.poll_target(target)

    assert result["newTweets"] == 0
    assert result["newFollowing"] == 0
    assert set(result["snapshotted"]) == {"tweets", "following"}
    assert notifier.sent == 0
    insights = storage.following_insights(min_common=1)
    assert insights["summary"]["profiledAccounts"] == 1
    assert insights["projects"] == []


def test_second_poll_creates_events_for_new_items(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("alice")
    client = FakeClient()
    notifier = FakeNotifier()
    poller = FakePoller(storage, _settings(storage.db_path), notifier, client)

    poller.poll_target(target)
    target = storage.get_target(target["id"])
    client.tweets.insert(
        0,
        Tweet(
            id="t2",
            text="second",
            author=Author(id="u1", name="Alice", screen_name="alice"),
            metrics=Metrics(),
            created_at="Fri May 22 01:00:00 +0000 2026",
        ),
    )
    client.following.insert(0, UserProfile(id="f2", name="Carol", screen_name="carol"))

    result = poller.poll_target(target)

    assert result["newTweets"] == 1
    assert result["newFollowing"] == 1
    assert notifier.sent == 2
    assert len(storage.list_events()) == 2


def test_poller_classifies_replies_and_retweets(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("alice")
    client = FakeClient()
    notifier = FakeNotifier()
    poller = FakePoller(storage, _settings(storage.db_path), notifier, client)

    poller.poll_target(target)
    target = storage.get_target(target["id"])
    client.tweets = [
        Tweet(
            id="t3",
            text="reply",
            author=Author(id="u1", name="Alice", screen_name="alice"),
            metrics=Metrics(),
            created_at="Fri May 22 02:00:00 +0000 2026",
            in_reply_to_status_id="root",
            in_reply_to_screen_name="bob",
        ),
        Tweet(
            id="t4",
            text="retweet",
            author=Author(id="u2", name="Carol", screen_name="carol"),
            metrics=Metrics(),
            created_at="Fri May 22 03:00:00 +0000 2026",
            is_retweet=True,
            retweeted_by="alice",
        ),
    ]

    result = poller.poll_target(target)
    event_types = {event["event_type"] for event in storage.list_events()}

    assert result["newReplies"] == 1
    assert result["newRetweets"] == 1
    assert event_types == {"reply", "retweet"}


def test_poller_applies_saved_network_proxy(monkeypatch, tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.update_notification_settings(telegram_proxy="http://proxy.local:7890")
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "auth")
    monkeypatch.setenv("TWITTER_CT0", "ct0")
    applied = []

    monkeypatch.setattr("twitter_monitor.poller.set_runtime_proxy", lambda proxy: applied.append(proxy))
    monkeypatch.setattr("twitter_monitor.poller.load_config", lambda: {"rateLimit": {}})
    monkeypatch.setattr("twitter_monitor.poller.TwitterClient", lambda *args, **kwargs: object())

    poller = MonitorPoller(storage, _settings(storage.db_path), FakeNotifier())

    poller._make_client()

    assert applied == ["http://proxy.local:7890"]


def test_notification_adapter_can_select_bark_channel(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.update_bark_settings(bark_device_keys=["device-key"])
    poller = MonitorPoller(storage, _settings(storage.db_path), FakeNotifier())

    adapter = poller._notification_adapter("bark")

    assert len(adapter.adapters) == 1
    assert isinstance(adapter.adapters[0], BarkNotifier)
    with pytest.raises(ValueError):
        poller._notification_adapter("unknown")


def test_notification_adapter_skips_disabled_channels(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.update_wxpusher_settings(
        wxpusher_app_token="app-token",
        wxpusher_uids=["UID_a"],
        wxpusher_enabled=False,
    )
    storage.update_bark_settings(bark_device_keys=["device-key"], bark_enabled=False)
    poller = MonitorPoller(storage, _settings(storage.db_path), FakeNotifier())

    assert poller._notification_adapter("wxpusher").adapters == []
    assert poller._notification_adapter("bark").adapters == []


def test_following_notification_uses_target_handle_and_time(monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "增长执行董事",
    )
    notifier = TelegramNotifier("token", "chat")

    text = notifier._format_event(
        {
            "event_type": "following",
            "target_handle": "CryptoZen911",
            "target_name": "CryptoZen",
            "target_group_name": "alpha猎手",
            "target_remark_name": "wx好友流星",
            "title": "Nina Rong (@nina_rong)",
            "body": "Executive Director of Growth",
            "url": "https://x.com/nina_rong",
            "detected_at": "2026-05-22T14:57:15Z",
            "payload_json": '{"name":"Nina Rong","screenName":"nina_rong"}',
        }
    )

    assert "@unknown" not in text
    assert "【alpha猎手】 wx好友流星" in text
    assert '<a href="https://x.com/CryptoZen911">CryptoZen（@CryptoZen911）</a>' in text
    assert '<a href="https://x.com/nina_rong">Nina Rong（@nina_rong）</a>' in text
    assert "原简介" in text
    assert "翻译简介" in text
    assert "增长执行董事" in text


def test_wxpusher_html_formatter_links_profiles_and_urls(monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "",
    )

    text = EventFormatter().format_wxpusher_html(
        {
            "event_type": "following",
            "target_handle": "CryptoZen911",
            "target_name": "CryptoZen",
            "target_group_name": "alpha猎手",
            "target_remark_name": "wx好友流星",
            "title": "Nina Rong (@nina_rong)",
            "body": "Executive Director of Growth @BNBChain",
            "url": "https://x.com/nina_rong",
            "detected_at": "2026-05-22T14:57:15Z",
            "payload_json": '{"name":"Nina Rong","screenName":"nina_rong"}',
        }
    )

    assert "<h3>" in text
    assert "【alpha猎手】 wx好友流星" in text
    assert '<a href="https://x.com/CryptoZen911">CryptoZen（@CryptoZen911）</a>' in text
    assert '<a href="https://x.com/nina_rong">Nina Rong（@nina_rong）</a>' in text
    assert '<a href="https://x.com/BNBChain">@BNBChain</a>' in text
    assert '<a href="https://x.com/nina_rong">https://x.com/nina_rong</a>' in text


def test_wxpusher_notifier_sends_html_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "",
    )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"code":1000}'

    class FakeOpener:
        def __init__(self) -> None:
            self.payload = None

        def open(self, request, timeout):
            self.payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

    opener = FakeOpener()
    notifier = WxPusherNotifier("app-token", ["UID_a"])
    notifier._opener = lambda: opener  # type: ignore[method-assign]

    result = notifier.send_event(
        {
            "event_type": "following",
            "target_handle": "CryptoZen911",
            "target_name": "CryptoZen",
            "target_group_name": "alpha猎手",
            "target_remark_name": "wx好友流星",
            "title": "Elon Musk (@elonmusk)",
            "body": "Founder @xAI",
            "url": "https://x.com/elonmusk",
            "detected_at": "2026-05-22T14:57:15Z",
            "payload_json": '{"name":"Elon Musk","screenName":"elonmusk"}',
        }
    )

    assert result.sent is True
    assert opener.payload["contentType"] == 2
    assert "alpha猎手｜wx好友流星｜CryptoZen（@CryptoZen911） 新增关注" in opener.payload["summary"]
    assert '<a href="https://x.com/elonmusk">Elon Musk（@elonmusk）</a>' in opener.payload["content"]


def test_bark_markdown_formatter_links_profiles_and_sections(monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "BNB Chain 增长负责人",
    )

    formatter = EventFormatter()
    text = formatter.format_bark_markdown(
        {
            "event_type": "following",
            "target_handle": "CryptoZen911",
            "target_name": "CryptoZen",
            "target_group_name": "alpha猎手",
            "target_remark_name": "wx好友流星",
            "title": "Nina Rong (@nina_rong)",
            "body": "Executive Director of Growth @BNBChain",
            "url": "https://x.com/nina_rong",
            "detected_at": "2026-05-22T14:57:15Z",
            "payload_json": '{"name":"Nina Rong","screenName":"nina_rong"}',
        }
    )

    assert text.startswith("### 【alpha猎手】 wx好友流星 · ")
    assert "[CryptoZen（@CryptoZen911）](https://x.com/CryptoZen911)" in text
    assert "于 2026\\-05\\-22 22:57:15 关注了" in text
    assert "[Nina Rong（@nina\\_rong）](https://x.com/nina_rong)" in text
    assert "**原简介**" in text
    assert "[@BNBChain](https://x.com/BNBChain)" in text
    assert "**翻译简介**" in text
    assert "BNB Chain 增长负责人" in text
    assert "[https://x\\.com/nina\\_rong](https://x.com/nina_rong)" in text


def test_bark_notifier_sends_critical_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "",
    )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"code":200,"message":"success"}'

    class FakeOpener:
        def __init__(self) -> None:
            self.payload = None
            self.url = ""

        def open(self, request, timeout):
            self.url = request.full_url
            self.payload = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

    opener = FakeOpener()
    notifier = BarkNotifier(
        "https://api.day.app",
        ["key-a", "key-b"],
        level="active",
        sound="minuet",
        group="XMonitor",
        call=True,
        volume=8,
    )
    notifier._opener = lambda: opener  # type: ignore[method-assign]

    result = notifier.send_event(
        {
            "event_type": "tweet",
            "target_handle": "alice",
            "target_name": "Alice",
            "target_group_name": "alpha",
            "target_remark_name": "friend",
            "title": "测试通知",
            "body": "hello",
            "url": "https://x.com/alice/status/1",
            "detected_at": "2026-05-22T14:57:15Z",
            "payload_json": "{}",
        }
    )

    assert result.sent is True
    assert opener.url == "https://api.day.app/push"
    payload = opener.payload
    assert payload["device_keys"] == ["key-a", "key-b"]
    assert payload["title"] == "【原创发推】alpha｜friend｜Alice（@alice）"
    assert "### 【alpha】 friend · [Alice（@alice）](https://x.com/alice)" in payload["markdown"]
    assert "**原文**" in payload["markdown"]
    assert "[https://x\\.com/alice/status/1](https://x.com/alice/status/1)" in payload["markdown"]
    assert payload["level"] == "critical"
    assert payload["call"] == "1"
    assert payload["sound"] == "minuet"
    assert payload["group"] == "XMonitor"
    assert payload["volume"] == "8"
    assert payload["url"] == "https://x.com/alice/status/1"


def test_telegram_notifier_sends_to_multiple_chats(monkeypatch) -> None:
    monkeypatch.setattr(
        "twitter_monitor.notifiers.LibreTranslateClient.translate_to_chinese",
        lambda self, text: "",
    )

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeOpener:
        def __init__(self) -> None:
            self.chat_ids = []

        def open(self, request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            self.chat_ids.append(payload["chat_id"])
            return FakeResponse()

    opener = FakeOpener()
    notifier = TelegramNotifier("token", ["owner", "-1001", "-1001"])
    notifier._opener = lambda: opener  # type: ignore[method-assign]

    result = notifier.send_event(
        {
            "event_type": "test",
            "target_handle": "monitor",
            "title": "测试",
            "body": "hello",
            "url": "",
        }
    )

    assert result.sent is True
    assert opener.chat_ids == ["owner", "-1001"]
