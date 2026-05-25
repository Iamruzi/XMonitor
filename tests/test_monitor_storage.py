from __future__ import annotations

import sqlite3

import pytest

from twitter_monitor.storage import MonitorStorage, normalize_handle


def test_normalize_handle_strips_at() -> None:
    assert normalize_handle("@SkyAAmen") == "SkyAAmen"


def test_normalize_handle_rejects_blank() -> None:
    with pytest.raises(ValueError):
        normalize_handle("   ")


def test_storage_adds_target_and_seen_state(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()

    target = storage.add_target("SkyAAmen", group_name="alpha猎手", remark_name="wx好友流星")

    assert target["handle"] == "SkyAAmen"
    assert target["group_name"] == "alpha猎手"
    assert target["remark_name"] == "wx好友流星"
    assert target["enabled"] is True
    assert target["monitor_retweets"] is True
    assert target["monitor_replies"] is True
    storage.add_seen_tweets(target["id"], ["1", "2"])
    assert storage.get_seen_tweet_ids(target["id"], ["1", "3"]) == {"1"}

    updated = storage.update_target(target["id"], {"group_name": "beta观察", "remark_name": "流星"})
    assert updated is not None
    assert updated["group_name"] == "beta观察"
    assert updated["remark_name"] == "流星"


def test_storage_rejects_duplicate_handles_case_insensitive(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.add_target("SkyAAmen")

    with pytest.raises(ValueError):
        storage.add_target("skyaamen")


def test_storage_create_event_is_idempotent(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("SkyAAmen")

    event = storage.create_event(
        target_id=target["id"],
        event_type="tweet",
        external_id="tweet-1",
        title="tweet",
        body="body",
    )
    duplicate = storage.create_event(
        target_id=target["id"],
        event_type="tweet",
        external_id="tweet-1",
        title="tweet",
        body="body",
    )

    assert event is not None
    assert event["target_handle"] == "SkyAAmen"
    assert event["target_group_name"] == ""
    assert event["target_remark_name"] == ""
    assert duplicate is None
    assert len(storage.list_events()) == 1


def test_storage_delete_target_cascades_state(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("SkyAAmen")
    storage.add_seen_following(target["id"], ["u1"])

    assert storage.delete_target(target["id"]) is True

    with sqlite3.connect(str(tmp_path / "monitor.db")) as conn:
        count = conn.execute("SELECT COUNT(*) FROM seen_following").fetchone()[0]
    assert count == 0


def test_storage_notification_settings_do_not_require_env(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()

    settings = storage.update_notification_settings(
        telegram_bot_token="token",
        telegram_chat_id="chat",
        telegram_proxy="http://127.0.0.1:7897",
    )

    assert settings["telegram_bot_token"] == "token"
    assert settings["telegram_chat_id"] == "chat"
    assert settings["telegram_proxy"] == "http://127.0.0.1:7897"

    settings = storage.update_notification_settings(clear_telegram_token=True)

    assert settings["telegram_bot_token"] == ""
    assert settings["telegram_chat_id"] == "chat"


def test_storage_wxpusher_settings_manage_uids(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()

    settings = storage.update_wxpusher_settings(
        wxpusher_app_token="app-token",
        wxpusher_uids=["UID_a", "UID_a", " UID_b "],
    )

    assert settings["wxpusher_app_token"] == "app-token"
    assert settings["wxpusher_uids"] == ["UID_a", "UID_b"]

    settings = storage.update_wxpusher_settings(wxpusher_add_uid="UID_c")
    assert settings["wxpusher_uids"] == ["UID_a", "UID_b", "UID_c"]

    settings = storage.update_wxpusher_settings(wxpusher_remove_uid="UID_b")
    assert settings["wxpusher_uids"] == ["UID_a", "UID_c"]

    settings = storage.update_wxpusher_settings(clear_wxpusher_app_token=True)
    assert settings["wxpusher_app_token"] == ""


def test_storage_telegram_authorized_chats_manage_crud(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()

    chats = storage.update_telegram_authorized_chats(add_chat_id="-1001", add_title="群 A")
    assert chats == [{"id": "-1001", "title": "群 A"}]
    assert storage.get_telegram_authorized_chat_ids() == ["-1001"]

    chats = storage.update_telegram_authorized_chats(add_chat_id="-1002", add_title="群 B")
    assert len(chats) == 2

    chats = storage.update_telegram_authorized_chats(update_chat_id="-1002", update_title="核心群")
    assert chats[1] == {"id": "-1002", "title": "核心群"}

    chats = storage.update_telegram_authorized_chats(remove_chat_id="-1001")
    assert chats == [{"id": "-1002", "title": "核心群"}]


def test_storage_manages_groups(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    first = storage.add_target("alice", group_name="alpha猎手")
    storage.add_target("bob", group_name="alpha猎手")
    storage.add_target("carol", group_name="beta观察", monitor_tweets=False)
    storage.update_target(first["id"], {"enabled": False})

    groups = storage.list_groups()

    assert groups[0]["name"] == "alpha猎手"
    assert groups[0]["count"] == 2
    assert groups[0]["enabledCount"] == 1
    storage.add_group("空分组")
    assert any(group["name"] == "空分组" and group["count"] == 0 for group in storage.list_groups())
    assert storage.rename_group("alpha猎手", "核心观察") == 2
    assert storage.get_target_by_handle("alice")["group_name"] == "核心观察"  # type: ignore[index]
    assert storage.clear_group("核心观察") == 2
    assert storage.get_target_by_handle("bob")["group_name"] == ""  # type: ignore[index]


def test_storage_manages_poll_settings(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()

    assert storage.get_poll_settings(default_min=180, default_max=300, default_backoff_max=1800) == {
        "pollIntervalMinSeconds": 180,
        "pollIntervalMaxSeconds": 300,
        "pollBackoffMaxSeconds": 1800,
    }

    settings = storage.update_poll_settings(min_seconds=20, max_seconds=10, backoff_max_seconds=5)

    assert settings == {
        "pollIntervalMinSeconds": 30,
        "pollIntervalMaxSeconds": 30,
        "pollBackoffMaxSeconds": 30,
    }
