"""SQLite persistence for monitor targets, state, and events."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_handle(handle: str) -> str:
    cleaned = handle.strip().lstrip("@")
    if not cleaned:
        raise ValueError("请填写要监控的用户名")
    if any(char.isspace() for char in cleaned):
        raise ValueError("用户名不能包含空格")
    return cleaned


class MonitorStorage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()

    def init(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    handle TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    user_id TEXT,
                    display_name TEXT,
                    group_name TEXT NOT NULL DEFAULT '',
                    remark_name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    monitor_tweets INTEGER NOT NULL DEFAULT 1,
                    monitor_retweets INTEGER NOT NULL DEFAULT 1,
                    monitor_replies INTEGER NOT NULL DEFAULT 1,
                    monitor_following INTEGER NOT NULL DEFAULT 1,
                    tweet_fetch_count INTEGER NOT NULL DEFAULT 10,
                    following_fetch_count INTEGER NOT NULL DEFAULT 40,
                    tweets_initialized INTEGER NOT NULL DEFAULT 0,
                    following_initialized INTEGER NOT NULL DEFAULT 0,
                    last_checked_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen_tweets (
                    target_id INTEGER NOT NULL,
                    tweet_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, tweet_id),
                    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS seen_following (
                    target_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, user_id),
                    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    detected_at TEXT NOT NULL,
                    notified_at TEXT,
                    notification_error TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE (target_id, event_type, external_id),
                    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_detected_at ON events(detected_at DESC);
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        target_columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)").fetchall()}
        migrations = {
            "monitor_retweets": "ALTER TABLE targets ADD COLUMN monitor_retweets INTEGER NOT NULL DEFAULT 1",
            "monitor_replies": "ALTER TABLE targets ADD COLUMN monitor_replies INTEGER NOT NULL DEFAULT 1",
            "group_name": "ALTER TABLE targets ADD COLUMN group_name TEXT NOT NULL DEFAULT ''",
            "remark_name": "ALTER TABLE targets ADD COLUMN remark_name TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in migrations.items():
            if column not in target_columns:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key in (
            "enabled",
            "monitor_tweets",
            "monitor_retweets",
            "monitor_replies",
            "monitor_following",
            "tweets_initialized",
            "following_initialized",
        ):
            if key in data:
                data[key] = bool(data[key])
        return data

    def add_target(
        self,
        handle: str,
        *,
        monitor_tweets: bool = True,
        monitor_retweets: bool = True,
        monitor_replies: bool = True,
        monitor_following: bool = True,
        tweet_fetch_count: int = 10,
        following_fetch_count: int = 40,
        group_name: str = "",
        remark_name: str = "",
    ) -> dict[str, Any]:
        handle = normalize_handle(handle)
        now = utc_now()
        with self._lock, self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO targets (
                        handle, group_name, remark_name, enabled,
                        monitor_tweets, monitor_retweets, monitor_replies, monitor_following,
                        tweet_fetch_count, following_fetch_count, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        handle,
                        self._clean_label(group_name),
                        self._clean_label(remark_name),
                        int(monitor_tweets),
                        int(monitor_retweets),
                        int(monitor_replies),
                        int(monitor_following),
                        max(int(tweet_fetch_count), 1),
                        max(int(following_fetch_count), 1),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个用户已经在监控列表里：@%s" % handle) from exc
            row = conn.execute("SELECT * FROM targets WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_dict(row) or {}

    def list_targets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM targets ORDER BY created_at DESC").fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def get_target(self, target_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        return self._row_to_dict(row)

    def get_target_by_handle(self, handle: str) -> dict[str, Any] | None:
        handle = normalize_handle(handle)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM targets WHERE handle = ? COLLATE NOCASE", (handle,)).fetchone()
        return self._row_to_dict(row)

    def update_target(self, target_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "enabled",
            "monitor_tweets",
            "monitor_retweets",
            "monitor_replies",
            "monitor_following",
            "tweet_fetch_count",
            "following_fetch_count",
            "group_name",
            "remark_name",
        }
        parts = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed or value is None:
                continue
            if key in {"enabled", "monitor_tweets", "monitor_retweets", "monitor_replies", "monitor_following"}:
                value = int(bool(value))
            if key in {"tweet_fetch_count", "following_fetch_count"}:
                value = max(int(value), 1)
            if key in {"group_name", "remark_name"}:
                value = self._clean_label(str(value))
            parts.append("%s = ?" % key)
            values.append(value)
        if not parts:
            return self.get_target(target_id)
        parts.append("updated_at = ?")
        values.append(utc_now())
        values.append(target_id)
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE targets SET %s WHERE id = ?" % ", ".join(parts), values)
        return self.get_target(target_id)

    def list_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    group_name AS name,
                    COUNT(*) AS count,
                    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_count,
                    MAX(updated_at) AS updated_at
                FROM targets
                WHERE TRIM(group_name) != ''
                GROUP BY group_name
                ORDER BY count DESC, group_name ASC
                """
            ).fetchall()
        groups = {
            str(row["name"]): {
                "name": str(row["name"]),
                "count": int(row["count"] or 0),
                "enabledCount": int(row["enabled_count"] or 0),
                "updatedAt": str(row["updated_at"] or ""),
            }
            for row in rows
        }
        for name in self.get_saved_groups():
            groups.setdefault(
                name,
                {
                    "name": name,
                    "count": 0,
                    "enabledCount": 0,
                    "updatedAt": "",
                },
            )
        return sorted(groups.values(), key=lambda item: (-int(item["count"]), str(item["name"])))

    def get_saved_groups(self) -> list[str]:
        raw = self.get_app_setting("monitor_groups", "[]")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = []
        if not isinstance(parsed, list):
            return []
        return self._normalize_group_list([str(item) for item in parsed])

    def add_group(self, group_name: str) -> list[dict[str, Any]]:
        label = self._clean_label(group_name)
        if not label:
            raise ValueError("请填写分组名称")
        groups = self.get_saved_groups()
        if label not in groups:
            groups.append(label)
            self._set_saved_groups(groups)
        return self.list_groups()

    def rename_group(self, old_name: str, new_name: str) -> int:
        old_label = self._clean_label(old_name)
        new_label = self._clean_label(new_name)
        if not old_label:
            raise ValueError("请选择要修改的分组")
        if not new_label:
            raise ValueError("新分组名不能为空")
        saved_groups = [new_label if group == old_label else group for group in self.get_saved_groups()]
        if old_label not in saved_groups and new_label not in saved_groups:
            saved_groups.append(new_label)
        self._set_saved_groups(saved_groups)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE targets
                SET group_name = ?, updated_at = ?
                WHERE group_name = ?
                """,
                (new_label, utc_now(), old_label),
            )
            return int(cur.rowcount)

    def clear_group(self, group_name: str) -> int:
        label = self._clean_label(group_name)
        if not label:
            raise ValueError("请选择要清空的分组")
        self._set_saved_groups([group for group in self.get_saved_groups() if group != label])
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE targets
                SET group_name = '', updated_at = ?
                WHERE group_name = ?
                """,
                (utc_now(), label),
            )
            return int(cur.rowcount)

    def get_poll_settings(
        self,
        *,
        default_min: int,
        default_max: int,
        default_backoff_max: int,
    ) -> dict[str, int]:
        min_seconds = self._int_setting("poll_interval_min_seconds", default_min, minimum=30)
        max_seconds = self._int_setting("poll_interval_max_seconds", default_max, minimum=min_seconds)
        backoff_max = self._int_setting("poll_backoff_max_seconds", default_backoff_max, minimum=max_seconds)
        return {
            "pollIntervalMinSeconds": min_seconds,
            "pollIntervalMaxSeconds": max_seconds,
            "pollBackoffMaxSeconds": backoff_max,
        }

    def update_poll_settings(
        self,
        *,
        min_seconds: int,
        max_seconds: int,
        backoff_max_seconds: int,
    ) -> dict[str, int]:
        min_value = max(int(min_seconds), 30)
        max_value = max(int(max_seconds), min_value)
        backoff_value = max(int(backoff_max_seconds), max_value)
        self.set_app_setting("poll_interval_min_seconds", str(min_value))
        self.set_app_setting("poll_interval_max_seconds", str(max_value))
        self.set_app_setting("poll_backoff_max_seconds", str(backoff_value))
        return {
            "pollIntervalMinSeconds": min_value,
            "pollIntervalMaxSeconds": max_value,
            "pollBackoffMaxSeconds": backoff_value,
        }

    def delete_target(self, target_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
            return cur.rowcount > 0

    def set_profile(self, target_id: int, *, user_id: str, handle: str, display_name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE targets
                SET user_id = ?, handle = ?, display_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (user_id, handle, display_name, utc_now(), target_id),
            )

    def set_checked(self, target_id: int, *, error: str | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE targets
                SET last_checked_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), error, utc_now(), target_id),
            )

    def set_initialized(self, target_id: int, *, tweets: bool | None = None, following: bool | None = None) -> None:
        updates = []
        values: list[Any] = []
        if tweets is not None:
            updates.append("tweets_initialized = ?")
            values.append(int(tweets))
        if following is not None:
            updates.append("following_initialized = ?")
            values.append(int(following))
        if not updates:
            return
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(target_id)
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE targets SET %s WHERE id = ?" % ", ".join(updates), values)

    def get_seen_tweet_ids(self, target_id: int, tweet_ids: list[str]) -> set[str]:
        return self._get_seen_ids("seen_tweets", "tweet_id", target_id, tweet_ids)

    def get_seen_following_ids(self, target_id: int, user_ids: list[str]) -> set[str]:
        return self._get_seen_ids("seen_following", "user_id", target_id, user_ids)

    def _get_seen_ids(self, table: str, column: str, target_id: int, values: list[str]) -> set[str]:
        if not values:
            return set()
        placeholders = ",".join("?" for _ in values)
        query = "SELECT %s FROM %s WHERE target_id = ? AND %s IN (%s)" % (
            column,
            table,
            column,
            placeholders,
        )
        with self._connect() as conn:
            rows = conn.execute(query, [target_id, *values]).fetchall()
        return {str(row[0]) for row in rows}

    def add_seen_tweets(self, target_id: int, tweet_ids: list[str]) -> None:
        self._add_seen("seen_tweets", "tweet_id", target_id, tweet_ids)

    def add_seen_following(self, target_id: int, user_ids: list[str]) -> None:
        self._add_seen("seen_following", "user_id", target_id, user_ids)

    def _add_seen(self, table: str, column: str, target_id: int, values: list[str]) -> None:
        rows = [(target_id, value, utc_now()) for value in values if value]
        if not rows:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO %s (target_id, %s, first_seen_at) VALUES (?, ?, ?)" % (table, column),
                rows,
            )

    def create_event(
        self,
        *,
        target_id: int,
        event_type: str,
        external_id: str,
        title: str,
        body: str,
        url: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    target_id, event_type, external_id, title, body, url, detected_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    event_type,
                    external_id,
                    title,
                    body,
                    url,
                    utc_now(),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            if cur.rowcount == 0:
                return None
            event_id = cur.lastrowid
            if event_id is None:
                return None
            row = self._get_event_row(conn, int(event_id))
        return self._row_to_dict(row)

    def mark_event_notified(self, event_id: int, *, error: str | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET notified_at = CASE WHEN ? IS NULL THEN ? ELSE notified_at END,
                    notification_error = ?
                WHERE id = ?
                """,
                (error, utc_now(), error, event_id),
            )

    def list_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT events.*,
                       targets.handle AS target_handle,
                       targets.display_name AS target_name,
                       targets.group_name AS target_group_name,
                       targets.remark_name AS target_remark_name
                FROM events
                JOIN targets ON targets.id = events.target_id
                ORDER BY events.detected_at DESC
                LIMIT ?
                """,
                (max(min(int(limit), 500), 1),),
            ).fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def _get_event_row(self, conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT events.*,
                   targets.handle AS target_handle,
                   targets.display_name AS target_name,
                   targets.group_name AS target_group_name,
                   targets.remark_name AS target_remark_name
            FROM events
            JOIN targets ON targets.id = events.target_id
            WHERE events.id = ?
            """,
            (event_id,),
        ).fetchone()

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            targets = conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
            enabled = conn.execute("SELECT COUNT(*) FROM targets WHERE enabled = 1").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM events WHERE notified_at IS NULL").fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM targets WHERE last_error IS NOT NULL").fetchone()[0]
            event_rows = conn.execute(
                "SELECT event_type, COUNT(*) AS count FROM events GROUP BY event_type"
            ).fetchall()
            group_rows = conn.execute(
                """
                SELECT group_name, COUNT(*) AS count
                FROM targets
                WHERE TRIM(group_name) != ''
                GROUP BY group_name
                ORDER BY count DESC, group_name ASC
                """
            ).fetchall()
        return {
            "targets": targets,
            "enabledTargets": enabled,
            "events": events,
            "pendingNotifications": pending,
            "targetsWithErrors": errors,
            "eventTypes": {str(row["event_type"]): int(row["count"]) for row in event_rows},
            "groups": {str(row["group_name"]): int(row["count"]) for row in group_rows},
        }

    def get_app_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def _int_setting(self, key: str, default: int, *, minimum: int = 1) -> int:
        raw = self.get_app_setting(key, "")
        try:
            parsed = int(raw)
        except ValueError:
            parsed = default
        return max(parsed, minimum)

    def set_app_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def get_notification_settings(self) -> dict[str, str]:
        return {
            "telegram_bot_token": self.get_app_setting("telegram_bot_token"),
            "telegram_chat_id": self.get_app_setting("telegram_chat_id"),
            "telegram_proxy": self.get_app_setting("telegram_proxy"),
            "telegram_authorized_chat_ids": self.get_app_setting("telegram_authorized_chat_ids", "[]"),
        }

    def update_notification_settings(
        self,
        *,
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
        telegram_proxy: str | None = None,
        clear_telegram_token: bool = False,
    ) -> dict[str, str]:
        if clear_telegram_token:
            self.set_app_setting("telegram_bot_token", "")
        elif telegram_bot_token is not None and telegram_bot_token.strip():
            self.set_app_setting("telegram_bot_token", telegram_bot_token.strip())
        if telegram_chat_id is not None:
            self.set_app_setting("telegram_chat_id", telegram_chat_id.strip())
        if telegram_proxy is not None:
            self.set_app_setting("telegram_proxy", telegram_proxy.strip())
        return self.get_notification_settings()

    def get_telegram_authorized_chat_ids(self) -> list[str]:
        return [chat["id"] for chat in self.get_telegram_authorized_chats()]

    def get_telegram_authorized_chats(self) -> list[dict[str, str]]:
        raw = self.get_app_setting("telegram_authorized_chats", "[]")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = []
        chats = []
        seen = set()
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                chat_id = str(item.get("id") or "").strip()
                if not chat_id or chat_id in seen:
                    continue
                chats.append({"id": chat_id, "title": str(item.get("title") or "").strip()})
                seen.add(chat_id)
        for chat_id in self._json_list_setting("telegram_authorized_chat_ids"):
            if chat_id not in seen:
                chats.append({"id": chat_id, "title": ""})
                seen.add(chat_id)
        return chats

    def update_telegram_authorized_chats(
        self,
        *,
        chat_ids: list[str] | None = None,
        add_chat_id: str | None = None,
        add_title: str = "",
        remove_chat_id: str | None = None,
        update_chat_id: str | None = None,
        update_title: str = "",
    ) -> list[dict[str, str]]:
        chats = self.get_telegram_authorized_chats()
        if chat_ids is not None:
            chats = [{"id": chat_id, "title": ""} for chat_id in self._normalize_uid_list(chat_ids)]
        if add_chat_id is not None and add_chat_id.strip():
            chat_id = add_chat_id.strip()
            found = False
            for chat in chats:
                if chat["id"] == chat_id:
                    found = True
                    if add_title.strip():
                        chat["title"] = add_title.strip()
                    break
            if not found:
                chats.append({"id": chat_id, "title": add_title.strip()})
        if remove_chat_id is not None and remove_chat_id.strip():
            remove = remove_chat_id.strip()
            chats = [chat for chat in chats if chat["id"] != remove]
        if update_chat_id is not None and update_chat_id.strip():
            target = update_chat_id.strip()
            for chat in chats:
                if chat["id"] == target:
                    chat["title"] = update_title.strip()
                    break
        self.set_app_setting(
            "telegram_authorized_chats",
            json.dumps(self._normalize_chat_objects(chats), ensure_ascii=False),
        )
        return self.get_telegram_authorized_chats()

    def set_telegram_authorized_chats(self, chats: list[dict[str, str]]) -> list[dict[str, str]]:
        self.set_app_setting(
            "telegram_authorized_chats",
            json.dumps(self._normalize_chat_objects(chats), ensure_ascii=False),
        )
        return self.get_telegram_authorized_chats()

    def get_wxpusher_settings(self) -> dict[str, Any]:
        return {
            "wxpusher_app_token": self.get_app_setting("wxpusher_app_token"),
            "wxpusher_uids": self._json_list_setting("wxpusher_uids"),
        }

    def update_wxpusher_settings(
        self,
        *,
        wxpusher_app_token: str | None = None,
        wxpusher_uids: list[str] | None = None,
        wxpusher_add_uid: str | None = None,
        wxpusher_remove_uid: str | None = None,
        clear_wxpusher_app_token: bool = False,
    ) -> dict[str, Any]:
        if clear_wxpusher_app_token:
            self.set_app_setting("wxpusher_app_token", "")
        elif wxpusher_app_token is not None and wxpusher_app_token.strip():
            self.set_app_setting("wxpusher_app_token", wxpusher_app_token.strip())

        uids = self._json_list_setting("wxpusher_uids")
        if wxpusher_uids is not None:
            uids = self._normalize_uid_list(wxpusher_uids)
        if wxpusher_add_uid is not None and wxpusher_add_uid.strip():
            uid = wxpusher_add_uid.strip()
            if uid not in uids:
                uids.append(uid)
        if wxpusher_remove_uid is not None and wxpusher_remove_uid.strip():
            remove_uid = wxpusher_remove_uid.strip()
            uids = [uid for uid in uids if uid != remove_uid]
        self.set_app_setting("wxpusher_uids", json.dumps(self._normalize_uid_list(uids), ensure_ascii=False))
        return self.get_wxpusher_settings()

    def _json_list_setting(self, key: str) -> list[str]:
        raw = self.get_app_setting(key, "[]")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return self._normalize_uid_list([str(item) for item in parsed])

    def _normalize_uid_list(self, values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            uid = str(value or "").strip()
            if not uid or uid in seen:
                continue
            result.append(uid)
            seen.add(uid)
        return result

    def _normalize_chat_objects(self, values: list[dict[str, str]]) -> list[dict[str, str]]:
        result = []
        seen = set()
        for value in values:
            chat_id = str(value.get("id") or "").strip()
            if not chat_id or chat_id in seen:
                continue
            result.append({"id": chat_id, "title": str(value.get("title") or "").strip()})
            seen.add(chat_id)
        return result

    def _normalize_group_list(self, values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            group = self._clean_label(value)
            if not group or group in seen:
                continue
            result.append(group)
            seen.add(group)
        return result

    def _set_saved_groups(self, values: list[str]) -> None:
        self.set_app_setting("monitor_groups", json.dumps(self._normalize_group_list(values), ensure_ascii=False))

    def _clean_label(self, value: str, limit: int = 80) -> str:
        return " ".join(str(value or "").split())[:limit]
