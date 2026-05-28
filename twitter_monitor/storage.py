"""SQLite persistence for monitor targets, state, and events."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after(seconds: int | float) -> str:
    value = max(float(seconds), 0.0)
    return (
        (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=value))
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_handle(handle: str) -> str:
    cleaned = handle.strip().lstrip("@")
    if not cleaned:
        raise ValueError("请填写要监控的用户名")
    if any(char.isspace() for char in cleaned):
        raise ValueError("用户名不能包含空格")
    return cleaned


PROJECT_KEYWORD_GROUPS = {
    "公链/协议": (
        "protocol",
        "network",
        "chain",
        "blockchain",
        "layer 1",
        "layer1",
        "layer 2",
        "layer2",
        "l1",
        "l2",
        "rollup",
        "mainnet",
        "testnet",
        "zk",
        "evm",
    ),
    "DeFi/交易": (
        "defi",
        "dex",
        "exchange",
        "swap",
        "staking",
        "liquidity",
        "yield",
        "lending",
        "perp",
        "oracle",
    ),
    "AI/Web3": (
        "ai",
        "agent",
        "agents",
        "web3",
        "crypto",
        "onchain",
        "wallet",
        "infrastructure",
        "infra",
        "dapp",
        "dao",
        "ecosystem",
    ),
    "NFT/GameFi": (
        "nft",
        "gamefi",
        "gaming",
        "metaverse",
        "collectibles",
    ),
}

PROJECT_HINTS = (
    "official",
    "foundation",
    "labs",
    "lab",
    "studio",
    "build",
    "powered by",
    "token",
    "airdrop",
    "launchpad",
    "社区",
    "官方",
    "协议",
    "生态",
    "链",
    "钱包",
    "交易所",
)

STRONG_PROJECT_TERMS = {
    "protocol",
    "network",
    "blockchain",
    "layer 1",
    "layer1",
    "layer 2",
    "layer2",
    "rollup",
    "mainnet",
    "testnet",
    "evm",
    "dex",
    "exchange",
    "swap",
    "staking",
    "liquidity",
    "yield",
    "lending",
    "perp",
    "perps",
    "oracle",
    "onchain",
    "on-chain",
    "wallet",
    "dapp",
    "dao",
    "foundation",
    "official",
    "launchpad",
    "协议",
    "官方",
    "交易所",
    "钱包",
}

HANDLE_PROJECT_TERMS = {
    "protocol",
    "foundation",
    "labs",
    "network",
    "chain",
    "wallet",
    "exchange",
    "dex",
    "dao",
    "defi",
    "app",
}

PERSON_HINTS = (
    "founder",
    "co-founder",
    "investor",
    "angel",
    "researcher",
    "engineer",
    "developer",
    "writer",
    "content creator",
    "kol",
    "ambassador",
    "trader",
    "partner",
    "advisor",
    "my views",
    "opinions",
    "not financial advice",
    "交易员",
    "猎手",
    "撸毛",
    "个人",
    "非投资建议",
    "邀请码",
)

HUNTER_HINTS = (
    "alpha",
    "airdrop",
    "hunter",
    "researcher",
    "analyst",
    "trader",
    "degen",
    "onchain",
    "on-chain",
    "builder",
    "founder",
    "co-founder",
    "angel",
    "investor",
    "vc",
    "defi",
    "crypto",
    "web3",
    "链上",
    "空投",
    "撸毛",
    "猎手",
    "研究员",
    "交易员",
    "投研",
    "一级",
)

HUNTER_NOISE_HINTS = (
    "official",
    "foundation",
    "protocol",
    "network",
    "exchange",
    "wallet",
    "launchpad",
    "community",
    "support",
    "news",
    "media",
    "招聘",
    "客服",
    "官方",
    "社区",
)

POLL_TASK_TYPES = {"tweets", "following"}


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
                CREATE TABLE IF NOT EXISTS poll_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 0,
                    run_after TEXT NOT NULL,
                    leased_until TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_started_at TEXT,
                    last_finished_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (target_id, task_type),
                    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_poll_tasks_due
                    ON poll_tasks(status, run_after, priority DESC);
                CREATE INDEX IF NOT EXISTS idx_poll_tasks_target
                    ON poll_tasks(target_id);
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
                CREATE INDEX IF NOT EXISTS idx_seen_following_user_id ON seen_following(user_id);
                CREATE TABLE IF NOT EXISTS followed_users (
                    user_id TEXT PRIMARY KEY,
                    screen_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    bio TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    followers_count INTEGER NOT NULL DEFAULT 0,
                    following_count INTEGER NOT NULL DEFAULT 0,
                    tweets_count INTEGER NOT NULL DEFAULT 0,
                    likes_count INTEGER NOT NULL DEFAULT 0,
                    verified INTEGER NOT NULL DEFAULT 0,
                    profile_image_url TEXT NOT NULL DEFAULT '',
                    profile_created_at TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_followed_users_screen_name
                    ON followed_users(screen_name COLLATE NOCASE);
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
        self._backfill_followed_users_from_events(conn)

    def _backfill_followed_users_from_events(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT external_id, title, body, url, detected_at, payload_json
            FROM events
            WHERE event_type = 'following'
            """
        ).fetchall()
        profile_rows = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            profile_rows.append(
                self._followed_user_profile_row(
                    user_id=str(payload.get("id") or row["external_id"] or ""),
                    screen_name=str(payload.get("screenName") or self._handle_from_url(row["url"]) or ""),
                    name=str(payload.get("name") or self._name_from_following_title(row["title"]) or ""),
                    bio=str(payload.get("bio") or row["body"] or ""),
                    location=str(payload.get("location") or ""),
                    url=str(payload.get("url") or row["url"] or ""),
                    followers_count=self._safe_int(payload.get("followers")),
                    following_count=self._safe_int(payload.get("following")),
                    tweets_count=self._safe_int(payload.get("tweets")),
                    likes_count=self._safe_int(payload.get("likes")),
                    verified=bool(payload.get("verified", False)),
                    profile_image_url=str(payload.get("profileImageUrl") or ""),
                    profile_created_at=str(payload.get("createdAt") or ""),
                    seen_at=str(row["detected_at"] or utc_now()),
                )
            )
        self._upsert_followed_user_rows(conn, profile_rows)

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

    def _poll_task_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        target_label = self._target_brief_label(
            {
                "groupName": row["target_group_name"] if "target_group_name" in row.keys() else "",
                "remarkName": row["target_remark_name"] if "target_remark_name" in row.keys() else "",
                "handle": row["target_handle"] if "target_handle" in row.keys() else "",
                "displayName": row["target_display_name"] if "target_display_name" in row.keys() else "",
            }
        )
        return {
            "id": int(row["id"]),
            "targetId": int(row["target_id"]),
            "targetHandle": str(row["target_handle"] if "target_handle" in row.keys() else ""),
            "targetDisplayName": str(row["target_display_name"] if "target_display_name" in row.keys() else ""),
            "targetGroupName": str(row["target_group_name"] if "target_group_name" in row.keys() else ""),
            "targetRemarkName": str(row["target_remark_name"] if "target_remark_name" in row.keys() else ""),
            "targetLabel": target_label if target_label != "未分组｜unknown（@unknown）" else "",
            "taskType": str(row["task_type"]),
            "status": str(row["status"]),
            "priority": int(row["priority"] or 0),
            "runAfter": str(row["run_after"] or ""),
            "leasedUntil": str(row["leased_until"] or ""),
            "attempts": int(row["attempts"] or 0),
            "lastStartedAt": str(row["last_started_at"] or ""),
            "lastFinishedAt": str(row["last_finished_at"] or ""),
            "lastError": str(row["last_error"] or ""),
            "createdAt": str(row["created_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def _poll_tasks_by_target(self, rows: list[sqlite3.Row]) -> dict[int, list[dict[str, Any]]]:
        tasks: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            task = self._poll_task_to_dict(row)
            tasks.setdefault(int(task["targetId"]), []).append(task)
        return tasks

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
            task_rows = conn.execute("SELECT * FROM poll_tasks ORDER BY task_type ASC").fetchall()
        targets = [self._row_to_dict(row) or {} for row in rows]
        tasks_by_target = self._poll_tasks_by_target(task_rows)
        for target in targets:
            target["pollTasks"] = tasks_by_target.get(int(target["id"]), [])
        return targets

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

    def sync_poll_tasks(
        self,
        *,
        tweet_interval_seconds: int,
        following_interval_seconds: int,
    ) -> dict[str, Any]:
        del tweet_interval_seconds, following_interval_seconds
        now = utc_now()
        with self._lock, self._connect() as conn:
            targets = conn.execute(
                """
                SELECT id, monitor_tweets, monitor_following, tweets_initialized, following_initialized
                FROM targets
                WHERE enabled = 1
                """
            ).fetchall()
            active_keys = set()
            rows = []
            for target in targets:
                target_id = int(target["id"])
                if int(target["monitor_tweets"]):
                    active_keys.add((target_id, "tweets"))
                    rows.append(
                        (
                            target_id,
                            "tweets",
                            40 if not int(target["tweets_initialized"]) else 20,
                            now,
                            now,
                            now,
                        )
                    )
                if int(target["monitor_following"]):
                    active_keys.add((target_id, "following"))
                    rows.append(
                        (
                            target_id,
                            "following",
                            30 if not int(target["following_initialized"]) else 10,
                            now,
                            now,
                            now,
                        )
                    )
            if rows:
                conn.executemany(
                    """
                    INSERT INTO poll_tasks (
                        target_id, task_type, priority, run_after, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(target_id, task_type) DO UPDATE SET
                        priority = excluded.priority,
                        updated_at = excluded.updated_at
                    """,
                    rows,
                )
            existing = conn.execute("SELECT target_id, task_type FROM poll_tasks").fetchall()
            stale = [
                (int(row["target_id"]), str(row["task_type"]))
                for row in existing
                if (int(row["target_id"]), str(row["task_type"])) not in active_keys
            ]
            conn.executemany(
                "DELETE FROM poll_tasks WHERE target_id = ? AND task_type = ?",
                stale,
            )
        return self.poll_queue_status()

    def acquire_due_poll_tasks(self, *, limit: int = 1, lease_seconds: int = 900) -> list[dict[str, Any]]:
        now = utc_now()
        lease_until = utc_after(lease_seconds)
        result = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT poll_tasks.*
                FROM poll_tasks
                JOIN targets ON targets.id = poll_tasks.target_id
                WHERE targets.enabled = 1
                  AND (
                    (poll_tasks.status = 'queued' AND poll_tasks.run_after <= ?)
                    OR (poll_tasks.status = 'running' AND poll_tasks.leased_until <= ?)
                  )
                  AND (
                    (poll_tasks.task_type = 'tweets' AND targets.monitor_tweets = 1)
                    OR (poll_tasks.task_type = 'following' AND targets.monitor_following = 1)
                  )
                ORDER BY poll_tasks.priority DESC, poll_tasks.run_after ASC, poll_tasks.id ASC
                LIMIT ?
                """,
                (now, now, max(int(limit), 1)),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE poll_tasks
                    SET status = 'running',
                        leased_until = ?,
                        last_started_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (lease_until, now, now, int(row["id"])),
                )
                target_row = conn.execute("SELECT * FROM targets WHERE id = ?", (int(row["target_id"]),)).fetchone()
                task = self._poll_task_to_dict(row)
                task["status"] = "running"
                task["leasedUntil"] = lease_until
                task["lastStartedAt"] = now
                task["target"] = self._row_to_dict(target_row) or {}
                result.append(task)
        return result

    def complete_poll_task(
        self,
        task_id: int,
        *,
        success: bool,
        next_run_after: str,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE poll_tasks
                    SET status = 'queued',
                        run_after = ?,
                        leased_until = NULL,
                        attempts = 0,
                        last_finished_at = ?,
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (next_run_after, now, now, int(task_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE poll_tasks
                    SET status = 'queued',
                        run_after = ?,
                        leased_until = NULL,
                        attempts = attempts + 1,
                        last_finished_at = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (next_run_after, now, str(error or "任务失败")[:500], now, int(task_id)),
                )

    def defer_poll_task(self, task_id: int, *, next_run_after: str, error: str | None = None) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE poll_tasks
                SET status = 'queued',
                    run_after = ?,
                    leased_until = NULL,
                    last_error = COALESCE(?, last_error),
                    updated_at = ?
                WHERE id = ?
                """,
                (next_run_after, error[:500] if error else None, now, int(task_id)),
            )

    def poll_queue_status(self, *, limit: int = 30) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as conn:
            count_rows = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'queued' AND run_after <= ? THEN 1 ELSE 0 END) AS due_count,
                    SUM(CASE WHEN status = 'queued' AND run_after > ? THEN 1 ELSE 0 END) AS queued_count,
                    SUM(CASE WHEN status = 'running' AND (leased_until IS NULL OR leased_until > ?) THEN 1 ELSE 0 END)
                        AS running_count,
                    SUM(CASE WHEN status = 'running' AND leased_until <= ? THEN 1 ELSE 0 END) AS stale_count,
                    SUM(CASE WHEN last_error IS NOT NULL AND last_error != '' THEN 1 ELSE 0 END) AS error_count,
                    COUNT(*) AS total_count
                FROM poll_tasks
                """,
                (now, now, now, now),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT poll_tasks.*,
                       targets.handle AS target_handle,
                       targets.display_name AS target_display_name,
                       targets.group_name AS target_group_name,
                       targets.remark_name AS target_remark_name,
                       targets.enabled AS target_enabled
                FROM poll_tasks
                JOIN targets ON targets.id = poll_tasks.target_id
                ORDER BY
                    CASE
                        WHEN poll_tasks.status = 'running' THEN 0
                        WHEN poll_tasks.run_after <= ? THEN 1
                        ELSE 2
                    END,
                    poll_tasks.run_after ASC,
                    poll_tasks.priority DESC,
                    poll_tasks.id ASC
                LIMIT ?
                """,
                (now, max(min(int(limit), 100), 1)),
            ).fetchall()
        summary = {
            "due": int(count_rows["due_count"] or 0),
            "queued": int(count_rows["queued_count"] or 0),
            "running": int(count_rows["running_count"] or 0),
            "stale": int(count_rows["stale_count"] or 0),
            "errors": int(count_rows["error_count"] or 0),
            "total": int(count_rows["total_count"] or 0),
            "generatedAt": now,
        }
        return {
            "summary": summary,
            "tasks": [self._poll_task_to_dict(row) for row in rows],
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

    def upsert_followed_users(self, users: list[Any]) -> None:
        rows = []
        for user in users:
            rows.append(
                self._followed_user_profile_row(
                    user_id=str(getattr(user, "id", "") or ""),
                    screen_name=str(getattr(user, "screen_name", "") or ""),
                    name=str(getattr(user, "name", "") or ""),
                    bio=str(getattr(user, "bio", "") or ""),
                    location=str(getattr(user, "location", "") or ""),
                    url=str(getattr(user, "url", "") or ""),
                    followers_count=self._safe_int(getattr(user, "followers_count", 0)),
                    following_count=self._safe_int(getattr(user, "following_count", 0)),
                    tweets_count=self._safe_int(getattr(user, "tweets_count", 0)),
                    likes_count=self._safe_int(getattr(user, "likes_count", 0)),
                    verified=bool(getattr(user, "verified", False)),
                    profile_image_url=str(getattr(user, "profile_image_url", "") or ""),
                    profile_created_at=str(getattr(user, "created_at", "") or ""),
                    seen_at=utc_now(),
                )
            )
        if not rows:
            return
        with self._lock, self._connect() as conn:
            self._upsert_followed_user_rows(conn, rows)

    def _add_seen(self, table: str, column: str, target_id: int, values: list[str]) -> None:
        rows = [(target_id, value, utc_now()) for value in values if value]
        if not rows:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO %s (target_id, %s, first_seen_at) VALUES (?, ?, ?)" % (table, column),
                rows,
            )

    def _followed_user_profile_row(
        self,
        *,
        user_id: str,
        screen_name: str,
        name: str,
        bio: str,
        location: str,
        url: str,
        followers_count: int,
        following_count: int,
        tweets_count: int,
        likes_count: int,
        verified: bool,
        profile_image_url: str,
        profile_created_at: str,
        seen_at: str,
    ) -> tuple[Any, ...] | None:
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        return (
            user_id,
            self._clean_label(screen_name, limit=120),
            self._clean_label(name, limit=160),
            str(bio or "").strip()[:800],
            self._clean_label(location, limit=160),
            str(url or "").strip()[:500],
            max(int(followers_count), 0),
            max(int(following_count), 0),
            max(int(tweets_count), 0),
            max(int(likes_count), 0),
            int(bool(verified)),
            str(profile_image_url or "").strip()[:500],
            str(profile_created_at or "").strip()[:120],
            seen_at,
            seen_at,
            seen_at,
        )

    def _upsert_followed_user_rows(
        self,
        conn: sqlite3.Connection,
        rows: list[tuple[Any, ...] | None],
    ) -> None:
        clean_rows = [row for row in rows if row is not None]
        if not clean_rows:
            return
        conn.executemany(
            """
            INSERT INTO followed_users (
                user_id, screen_name, name, bio, location, url,
                followers_count, following_count, tweets_count, likes_count, verified,
                profile_image_url, profile_created_at, first_seen_at, last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                screen_name = CASE
                    WHEN excluded.screen_name != '' THEN excluded.screen_name
                    ELSE followed_users.screen_name
                END,
                name = CASE
                    WHEN excluded.name != '' THEN excluded.name
                    ELSE followed_users.name
                END,
                bio = CASE
                    WHEN excluded.bio != '' THEN excluded.bio
                    ELSE followed_users.bio
                END,
                location = CASE
                    WHEN excluded.location != '' THEN excluded.location
                    ELSE followed_users.location
                END,
                url = CASE
                    WHEN excluded.url != '' THEN excluded.url
                    ELSE followed_users.url
                END,
                followers_count = excluded.followers_count,
                following_count = excluded.following_count,
                tweets_count = excluded.tweets_count,
                likes_count = excluded.likes_count,
                verified = excluded.verified,
                profile_image_url = CASE
                    WHEN excluded.profile_image_url != '' THEN excluded.profile_image_url
                    ELSE followed_users.profile_image_url
                END,
                profile_created_at = CASE
                    WHEN excluded.profile_created_at != '' THEN excluded.profile_created_at
                    ELSE followed_users.profile_created_at
                END,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            clean_rows,
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

    def following_insights(
        self,
        *,
        group_name: str = "",
        min_common: int = 2,
        limit: int = 80,
    ) -> dict[str, Any]:
        selected_group = self._clean_label(group_name)
        min_common = max(int(min_common), 2)
        limit = max(min(int(limit), 200), 1)
        with self._connect() as conn:
            target_rows = conn.execute(
                """
                SELECT id, handle, user_id, display_name, group_name, remark_name, enabled,
                       following_initialized, last_checked_at, last_error
                FROM targets
                ORDER BY group_name ASC, created_at DESC
                """
            ).fetchall()
            relation_rows = conn.execute(
                """
                SELECT seen_following.user_id,
                       seen_following.first_seen_at,
                       targets.id AS target_id,
                       targets.handle AS target_handle,
                       targets.display_name AS target_display_name,
                       targets.group_name AS target_group_name,
                       targets.remark_name AS target_remark_name,
                       targets.enabled AS target_enabled
                FROM seen_following
                JOIN targets ON targets.id = seen_following.target_id
                ORDER BY seen_following.first_seen_at DESC
                """
            ).fetchall()
            profile_rows = conn.execute("SELECT * FROM followed_users").fetchall()

        targets = [self._row_to_dict(row) or {} for row in target_rows]
        profiles = {str(row["user_id"]): dict(row) for row in profile_rows}
        visible_targets = [
            target for target in targets
            if not selected_group or str(target.get("group_name") or "") == selected_group
        ]
        visible_target_ids = {int(target["id"]) for target in visible_targets}
        visible_relations = [
            row for row in relation_rows
            if not selected_group or int(row["target_id"]) in visible_target_ids
        ]

        accounts = self._account_cards_from_relations(visible_relations, profiles)
        radar_accounts = [
            account for account in accounts
            if account["commonCount"] >= min_common
        ]
        radar_accounts.sort(key=self._account_sort_key)
        project_candidates = [account for account in radar_accounts if account["isProject"]]
        hunter_candidates = self._hunter_candidates(
            radar_accounts,
            targets=visible_targets,
            limit=limit,
        )

        group_cards = self._group_insight_cards(targets, relation_rows, profiles, min_common=min_common)
        followed_accounts = len(accounts)
        shared_accounts = sum(1 for account in accounts if account["commonCount"] >= 2)
        project_accounts = len(project_candidates)
        monitors_with_following = {
            int(row["target_id"]) for row in visible_relations if int(row["target_id"]) in visible_target_ids
        }
        return {
            "summary": {
                "groupName": selected_group,
                "monitoredUsers": len(visible_targets),
                "monitorsWithFollowing": len(monitors_with_following),
                "followedAccounts": followed_accounts,
                "profiledAccounts": sum(1 for account in accounts if account["profiled"]),
                "sharedAccounts": shared_accounts,
                "projectAccounts": project_accounts,
                "hunterCandidates": len(hunter_candidates),
                "relationships": len(visible_relations),
                "minCommon": min_common,
                "generatedAt": utc_now(),
            },
            "groups": group_cards,
            "accounts": radar_accounts[:limit],
            "projects": radar_accounts[:limit],
            "projectCandidates": project_candidates[:limit],
            "hunterCandidates": hunter_candidates,
        }

    def _account_cards_from_relations(
        self,
        relation_rows: list[sqlite3.Row],
        profiles: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        followers_by_user: dict[str, list[dict[str, Any]]] = {}
        first_seen: dict[str, str] = {}
        last_seen: dict[str, str] = {}
        for row in relation_rows:
            user_id = str(row["user_id"] or "")
            if not user_id:
                continue
            followers_by_user.setdefault(user_id, []).append(self._target_brief_from_relation(row))
            seen_at = str(row["first_seen_at"] or "")
            if seen_at and (not first_seen.get(user_id) or seen_at < first_seen[user_id]):
                first_seen[user_id] = seen_at
            if seen_at and (not last_seen.get(user_id) or seen_at > last_seen[user_id]):
                last_seen[user_id] = seen_at
        return [
            self._followed_account_card(
                user_id=user_id,
                profile=profiles.get(user_id),
                followed_by=followed_by,
                first_seen_at=first_seen.get(user_id, ""),
                last_seen_at=last_seen.get(user_id, ""),
            )
            for user_id, followed_by in followers_by_user.items()
        ]

    def _group_insight_cards(
        self,
        targets: list[dict[str, Any]],
        relation_rows: list[sqlite3.Row],
        profiles: dict[str, dict[str, Any]],
        *,
        min_common: int,
    ) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for name in self.get_saved_groups():
            groups.setdefault(
                name,
                {
                    "name": name,
                    "targets": [],
                    "enabledCount": 0,
                    "relations": [],
                },
            )
        for target in targets:
            name = str(target.get("group_name") or "未分组")
            group = groups.setdefault(
                name,
                {
                    "name": name,
                    "targets": [],
                    "enabledCount": 0,
                    "relations": [],
                },
            )
            group["targets"].append(self._target_brief_from_target(target))
            group["enabledCount"] += int(bool(target.get("enabled")))
        for row in relation_rows:
            name = str(row["target_group_name"] or "未分组")
            group = groups.setdefault(
                name,
                {
                    "name": name,
                    "targets": [],
                    "enabledCount": 0,
                    "relations": [],
                },
            )
            group["relations"].append(row)

        cards = []
        for name, group in groups.items():
            accounts = self._account_cards_from_relations(group["relations"], profiles)
            shared_accounts = [
                account for account in accounts
                if account["commonCount"] >= min_common
            ]
            shared_accounts.sort(key=self._account_sort_key)
            cards.append(
                {
                    "name": name,
                    "targetCount": len(group["targets"]),
                    "enabledCount": int(group["enabledCount"]),
                    "followingAccounts": len(accounts),
                    "sharedAccounts": sum(1 for account in accounts if account["commonCount"] >= 2),
                    "projectAccounts": sum(1 for account in shared_accounts if account["isProject"]),
                    "targets": group["targets"],
                    "topAccounts": shared_accounts[:8],
                    "topProjects": shared_accounts[:8],
                }
            )
        return sorted(cards, key=lambda item: (-int(item["targetCount"]), str(item["name"])))

    def _followed_account_card(
        self,
        *,
        user_id: str,
        profile: dict[str, Any] | None,
        followed_by: list[dict[str, Any]],
        first_seen_at: str,
        last_seen_at: str,
    ) -> dict[str, Any]:
        profile = profile or {}
        handle = str(profile.get("screen_name") or user_id)
        name = str(profile.get("name") or handle)
        bio = str(profile.get("bio") or "")
        url = str(profile.get("url") or "")
        followers_count = self._safe_int(profile.get("followers_count"))
        signal = self._project_signal(
            name=name,
            handle=handle,
            bio=bio,
            url=url,
            followers_count=followers_count,
            verified=bool(profile.get("verified")),
            profiled=bool(profile),
        )
        followed_by = sorted(
            followed_by,
            key=lambda item: (
                str(item.get("groupName") or ""),
                str(item.get("remarkName") or ""),
                str(item.get("handle") or ""),
            ),
        )
        discovery = self._project_discovery_signal(
            common_count=len(followed_by),
            followed_by=followed_by,
            followers_count=followers_count,
            project_score=int(signal.get("projectScore") or 0),
            verified=bool(profile.get("verified")),
            url=url,
            profile_created_at=str(profile.get("profile_created_at") or ""),
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
        )
        trend_events = self._project_trend_events(followed_by)
        return {
            "userId": user_id,
            "handle": handle,
            "name": name,
            "bio": bio,
            "summary": self._project_summary(bio=bio, url=url),
            "location": str(profile.get("location") or ""),
            "url": url,
            "followers": followers_count,
            "following": self._safe_int(profile.get("following_count")),
            "tweets": self._safe_int(profile.get("tweets_count")),
            "verified": bool(profile.get("verified")),
            "profileImageUrl": str(profile.get("profile_image_url") or ""),
            "profileCreatedAt": str(profile.get("profile_created_at") or ""),
            "firstSeenAt": first_seen_at,
            "lastSeenAt": last_seen_at,
            "lastProfileSeenAt": str(profile.get("last_seen_at") or ""),
            "profiled": bool(profile),
            "commonCount": len(followed_by),
            "followedBy": followed_by,
            "groupNames": sorted({str(item.get("groupName") or "") for item in followed_by if item.get("groupName")}),
            "isHot": bool(signal.get("isProject") and len(followed_by) >= 2),
            "trendEvents": trend_events,
            "latestTrendText": trend_events[-1]["text"] if trend_events else "",
            **discovery,
            **signal,
        }

    def _target_brief_from_target(self, target: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(target["id"]),
            "handle": str(target.get("handle") or ""),
            "displayName": str(target.get("display_name") or ""),
            "groupName": str(target.get("group_name") or ""),
            "remarkName": str(target.get("remark_name") or ""),
            "enabled": bool(target.get("enabled")),
            "followingInitialized": bool(target.get("following_initialized")),
            "lastCheckedAt": str(target.get("last_checked_at") or ""),
            "lastError": str(target.get("last_error") or ""),
        }

    def _target_brief_from_relation(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["target_id"]),
            "handle": str(row["target_handle"] or ""),
            "displayName": str(row["target_display_name"] or ""),
            "groupName": str(row["target_group_name"] or ""),
            "remarkName": str(row["target_remark_name"] or ""),
            "enabled": bool(row["target_enabled"]),
            "firstSeenAt": str(row["first_seen_at"] or ""),
        }

    def _account_sort_key(self, account: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -int(account.get("commonCount") or 0),
            -int(bool(account.get("isProject"))),
            -int(account.get("earlyScore") or 0),
            -int(account.get("projectScore") or 0),
            int(account.get("followers") or 0),
            str(account.get("handle") or ""),
        )

    def _hunter_candidates(
        self,
        accounts: list[dict[str, Any]],
        *,
        targets: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        monitored_handles = {
            str(target.get("handle") or "").lower()
            for target in targets
            if target.get("handle")
        }
        monitored_user_ids = {
            str(target.get("user_id") or "")
            for target in targets
            if target.get("user_id")
        }
        candidates = []
        for account in accounts:
            handle = str(account.get("handle") or "")
            user_id = str(account.get("userId") or "")
            if not account.get("profiled"):
                continue
            if account.get("isProject") or account.get("isUnresolved"):
                continue
            if handle.lower() in monitored_handles or user_id in monitored_user_ids:
                continue
            score_payload = self._hunter_signal(account)
            if int(score_payload["hunterScore"]) < 45:
                continue
            candidates.append({**account, **score_payload})
        candidates.sort(
            key=lambda item: (
                -int(item.get("hunterScore") or 0),
                -int(item.get("commonCount") or 0),
                -int(item.get("groupCount") or 0),
                int(item.get("followers") or 0),
                str(item.get("handle") or ""),
            )
        )
        return candidates[:limit]

    def _hunter_signal(self, account: dict[str, Any]) -> dict[str, Any]:
        handle = str(account.get("handle") or "")
        name = str(account.get("name") or "")
        bio = str(account.get("bio") or "")
        text = " ".join([handle, name, bio]).lower()
        hit_terms = [term for term in HUNTER_HINTS if self._term_in_text(text, term)]
        noise_terms = [term for term in HUNTER_NOISE_HINTS if self._term_in_text(text, term)]
        common_count = int(account.get("commonCount") or 0)
        group_count = int(account.get("groupCount") or 0)
        followers = int(account.get("followers") or 0)
        following = int(account.get("following") or 0)
        recent_7d = int(account.get("recentFollowCount7d") or 0)
        score = common_count * 18 + group_count * 12 + min(len(set(hit_terms)), 5) * 8
        reasons = []
        if common_count >= 2:
            reasons.append("被 %s 个已监控 alpha 关注" % common_count)
        if group_count >= 2:
            reasons.append("跨 %s 个分组出现" % group_count)
        if hit_terms:
            reasons.append("猎手画像：" + "、".join(sorted(set(hit_terms))[:4]))
        if recent_7d >= 2:
            score += 10
            reasons.append("7 天内集中出现")
        if 500 <= followers <= 150_000:
            score += 10
            reasons.append("账号体量适合早期观察")
        elif followers > 500_000:
            score -= 18
            reasons.append("粉丝过高，优先级下降")
        elif followers == 0:
            score -= 6
        if following >= 100:
            score += 6
            reasons.append("关注面足够宽")
        if account.get("verified"):
            score -= 6
        if noise_terms:
            score -= min(len(set(noise_terms)), 4) * 10
            reasons.append("噪音线索：" + "、".join(sorted(set(noise_terms))[:3]))
        confidence = "观察"
        if score >= 85:
            confidence = "强候选"
        elif score >= 65:
            confidence = "候选"
        if not reasons:
            reasons.append("共同关注关系较强，等待更多资料验证")
        return {
            "hunterScore": max(score, 0),
            "hunterConfidence": confidence,
            "hunterSignals": reasons[:5],
            "hunterMatchedTerms": sorted(set(hit_terms))[:8],
            "hunterNoiseTerms": sorted(set(noise_terms))[:5],
            "recommendation": "影子观察，不自动加入核心监控",
        }

    def _project_discovery_signal(
        self,
        *,
        common_count: int,
        followed_by: list[dict[str, Any]],
        followers_count: int,
        project_score: int,
        verified: bool,
        url: str,
        profile_created_at: str,
        first_seen_at: str,
        last_seen_at: str,
    ) -> dict[str, Any]:
        group_names = {
            str(item.get("groupName") or "未分组")
            for item in followed_by
        }
        now = datetime.now(timezone.utc)
        recent_24h = 0
        recent_7d = 0
        for item in followed_by:
            seen_at = self._parse_timestamp(str(item.get("firstSeenAt") or ""))
            if not seen_at:
                continue
            if seen_at >= now - timedelta(days=1):
                recent_24h += 1
            if seen_at >= now - timedelta(days=7):
                recent_7d += 1

        account_created_at = self._parse_timestamp(profile_created_at)
        account_age_days = (now - account_created_at).days if account_created_at else None
        early_score = common_count * 18 + min(len(group_names), 4) * 12 + project_score * 7
        signals = []
        if followers_count <= 0:
            follower_stage = "待补资料"
        elif followers_count < 10_000:
            follower_stage = "低粉早期"
            early_score += 28
            signals.append("低粉早期")
        elif followers_count < 50_000:
            follower_stage = "成长前段"
            early_score += 18
            signals.append("成长前段")
        elif followers_count < 200_000:
            follower_stage = "扩散中"
            early_score += 8
        else:
            follower_stage = "成熟项目"
            early_score -= 10
        if len(group_names) >= 2:
            early_score += 18
            signals.append("跨组共识")
        if recent_24h >= 2:
            early_score += 22
            signals.append("24h 集中关注")
        elif recent_7d >= 2:
            early_score += 12
            signals.append("7天内升温")
        if account_age_days is not None and account_age_days <= 365:
            early_score += 14
            signals.append("新账号")
        if url:
            early_score += 5
            signals.append("官网线索")
        if verified:
            early_score -= 4
        if not signals:
            signals.append("共同关注")

        return {
            "earlyScore": max(early_score, 0),
            "followerStage": follower_stage,
            "groupCount": len(group_names),
            "recentFollowCount24h": recent_24h,
            "recentFollowCount7d": recent_7d,
            "accountAgeDays": account_age_days,
            "discoverySignals": signals[:5],
            "firstSeenAt": first_seen_at,
            "lastSeenAt": last_seen_at,
        }

    def _project_trend_events(self, followed_by: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(
            followed_by,
            key=lambda item: (
                self._parse_timestamp(str(item.get("firstSeenAt") or "")) or datetime.min.replace(tzinfo=timezone.utc),
                str(item.get("handle") or ""),
            ),
        )
        events = []
        for index, target in enumerate(ordered, start=1):
            seen_at = str(target.get("firstSeenAt") or "")
            label = self._target_brief_label(target)
            verb = "关注了" if index == 1 else "也关注了"
            marker = "🔥" if index >= 2 else ""
            time_text = self._display_timestamp(seen_at)
            parts = [part for part in [marker, time_text, label, verb] if part]
            events.append(
                {
                    "sequence": index,
                    "firstSeenAt": seen_at,
                    "marker": marker,
                    "label": label,
                    "target": target,
                    "text": " ".join(parts),
                }
            )
        return events

    def followed_account_context(self, user_id: str) -> dict[str, Any] | None:
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        with self._connect() as conn:
            relation_rows = conn.execute(
                """
                SELECT seen_following.user_id,
                       seen_following.first_seen_at,
                       targets.id AS target_id,
                       targets.handle AS target_handle,
                       targets.display_name AS target_display_name,
                       targets.group_name AS target_group_name,
                       targets.remark_name AS target_remark_name,
                       targets.enabled AS target_enabled
                FROM seen_following
                JOIN targets ON targets.id = seen_following.target_id
                WHERE seen_following.user_id = ?
                ORDER BY seen_following.first_seen_at ASC
                """,
                (user_id,),
            ).fetchall()
            profile_row = conn.execute(
                "SELECT * FROM followed_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not relation_rows:
            return None
        profiles = {user_id: dict(profile_row)} if profile_row is not None else {}
        cards = self._account_cards_from_relations(relation_rows, profiles)
        return cards[0] if cards else None

    def _project_signal(
        self,
        *,
        name: str,
        handle: str,
        bio: str,
        url: str,
        followers_count: int,
        verified: bool,
        profiled: bool,
    ) -> dict[str, Any]:
        if not profiled:
            return {
                "isProject": False,
                "isUnresolved": True,
                "isPersonal": False,
                "category": "未识别账号",
                "projectScore": 0,
                "reason": "还没有采集到账号资料，不进入项目雷达",
            }
        if handle.isdigit() and not bio and not url:
            return {
                "isProject": False,
                "isUnresolved": True,
                "isPersonal": False,
                "category": "未识别账号",
                "projectScore": 0,
                "reason": "只有用户 ID，没有项目资料，不进入项目雷达",
            }
        text = " ".join([name, handle, bio, url]).lower()
        matched_terms = []
        category = "账号"
        for label, terms in PROJECT_KEYWORD_GROUPS.items():
            hits = [term for term in terms if self._term_in_text(text, term)]
            if hits:
                category = label
                matched_terms.extend(hits[:3])
        hint_hits = [term for term in PROJECT_HINTS if self._term_in_text(text, term)]
        person_hits = [term for term in PERSON_HINTS if self._term_in_text(text, term)]
        score = len(set(matched_terms)) * 2 + len(set(hint_hits))
        if verified:
            score += 1
        if url:
            score += 1
        if followers_count >= 50_000:
            score += 1
        if person_hits:
            score = max(score - 4, 0)
        all_hits = {*matched_terms, *hint_hits}
        strong_hits = STRONG_PROJECT_TERMS.intersection(all_hits)
        handle_hits = [term for term in HANDLE_PROJECT_TERMS if self._term_in_text(handle.lower(), term)]
        if handle_hits:
            score += 1
        is_project = bool(strong_hits) and score >= 3 and not person_hits
        if person_hits and len(strong_hits) >= 2 and score >= 7:
            is_project = True
        if is_project and category == "账号":
            category = "项目/组织"
        reasons = []
        if matched_terms:
            reasons.append("关键词：" + "、".join(sorted(set(matched_terms))[:4]))
        if hint_hits:
            reasons.append("线索：" + "、".join(sorted(set(hint_hits))[:3]))
        if person_hits and not is_project:
            reasons.append("更像个人账号：" + "、".join(sorted(set(person_hits))[:2]))
        if not reasons:
            reasons.append("未命中项目关键词，先按普通账号展示")
        return {
            "isProject": is_project,
            "isUnresolved": False,
            "isPersonal": bool(person_hits and not is_project),
            "category": category,
            "projectScore": score,
            "reason": "；".join(reasons),
        }

    def _term_in_text(self, text: str, term: str) -> bool:
        if term.isascii() and re.fullmatch(r"[a-z0-9][a-z0-9 +.-]*", term):
            pattern = r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term)
            return re.search(pattern, text) is not None
        return term in text

    def _project_summary(self, *, bio: str, url: str) -> str:
        bio = " ".join(str(bio or "").split())
        if bio:
            return bio[:220]
        if url:
            return "已采集主页：%s" % url
        return "还没有采集到简介，等待下一轮关注检查补全。"

    def _target_brief_label(self, target: dict[str, Any]) -> str:
        group_name = str(target.get("groupName") or "未分组")
        remark_name = str(target.get("remarkName") or "")
        handle = str(target.get("handle") or "unknown")
        display_name = str(target.get("displayName") or handle)
        parts = [group_name]
        if remark_name:
            parts.append(remark_name)
        parts.append("%s（@%s）" % (display_name, handle))
        return "｜".join(parts)

    def _display_timestamp(self, value: str) -> str:
        parsed = self._parse_timestamp(value)
        if not parsed:
            return str(value or "")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    def _parse_timestamp(self, value: str) -> datetime | None:
        value = str(value or "").strip()
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _handle_from_url(self, value: str) -> str:
        url = str(value or "").strip().rstrip("/")
        if not url:
            return ""
        return url.rsplit("/", 1)[-1].lstrip("@")

    def _name_from_following_title(self, value: str) -> str:
        title = str(value or "").strip()
        if "（@" in title:
            return title.split("（@", 1)[0].strip()
        if "(@" in title:
            return title.split("(@", 1)[0].strip()
        return title

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
            "wxpusher_enabled": self.get_app_setting("wxpusher_enabled", "1"),
            "wxpusher_hot_filter_enabled": self.get_app_setting("wxpusher_hot_filter_enabled", "0"),
            "wxpusher_hot_filter_min_common": self.get_app_setting("wxpusher_hot_filter_min_common", "2"),
        }

    def update_wxpusher_settings(
        self,
        *,
        wxpusher_app_token: str | None = None,
        wxpusher_uids: list[str] | None = None,
        wxpusher_add_uid: str | None = None,
        wxpusher_remove_uid: str | None = None,
        wxpusher_enabled: bool | None = None,
        wxpusher_hot_filter_enabled: bool | None = None,
        wxpusher_hot_filter_min_common: int | None = None,
        clear_wxpusher_app_token: bool = False,
    ) -> dict[str, Any]:
        if clear_wxpusher_app_token:
            self.set_app_setting("wxpusher_app_token", "")
        elif wxpusher_app_token is not None and wxpusher_app_token.strip():
            self.set_app_setting("wxpusher_app_token", wxpusher_app_token.strip())
        if wxpusher_enabled is not None:
            self.set_app_setting("wxpusher_enabled", "1" if wxpusher_enabled else "0")
        if wxpusher_hot_filter_enabled is not None:
            self.set_app_setting("wxpusher_hot_filter_enabled", "1" if wxpusher_hot_filter_enabled else "0")
        if wxpusher_hot_filter_min_common is not None:
            self.set_app_setting("wxpusher_hot_filter_min_common", str(max(int(wxpusher_hot_filter_min_common), 2)))

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

    def get_bark_settings(self) -> dict[str, Any]:
        return {
            "bark_server_url": self.get_app_setting("bark_server_url"),
            "bark_device_keys": self._json_list_setting("bark_device_keys"),
            "bark_level": self.get_app_setting("bark_level"),
            "bark_sound": self.get_app_setting("bark_sound"),
            "bark_group": self.get_app_setting("bark_group"),
            "bark_call": self.get_app_setting("bark_call"),
            "bark_volume": self.get_app_setting("bark_volume"),
            "bark_enabled": self.get_app_setting("bark_enabled", "1"),
            "bark_hot_filter_enabled": self.get_app_setting("bark_hot_filter_enabled", "0"),
            "bark_hot_filter_min_common": self.get_app_setting("bark_hot_filter_min_common", "2"),
        }

    def update_bark_settings(
        self,
        *,
        bark_server_url: str | None = None,
        bark_device_keys: list[str] | None = None,
        bark_add_device_key: str | None = None,
        bark_remove_device_key: str | None = None,
        bark_level: str | None = None,
        bark_sound: str | None = None,
        bark_group: str | None = None,
        bark_call: bool | None = None,
        bark_volume: int | None = None,
        bark_enabled: bool | None = None,
        bark_hot_filter_enabled: bool | None = None,
        bark_hot_filter_min_common: int | None = None,
    ) -> dict[str, Any]:
        if bark_server_url is not None:
            self.set_app_setting("bark_server_url", self._clean_url(bark_server_url))
        if bark_level is not None:
            self.set_app_setting("bark_level", self._normalize_bark_level(bark_level))
        if bark_sound is not None:
            self.set_app_setting("bark_sound", self._clean_label(bark_sound, limit=80))
        if bark_group is not None:
            self.set_app_setting("bark_group", self._clean_label(bark_group, limit=80))
        if bark_call is not None:
            self.set_app_setting("bark_call", "1" if bark_call else "0")
        if bark_volume is not None:
            self.set_app_setting("bark_volume", str(min(max(int(bark_volume), 0), 10)))
        if bark_enabled is not None:
            self.set_app_setting("bark_enabled", "1" if bark_enabled else "0")
        if bark_hot_filter_enabled is not None:
            self.set_app_setting("bark_hot_filter_enabled", "1" if bark_hot_filter_enabled else "0")
        if bark_hot_filter_min_common is not None:
            self.set_app_setting("bark_hot_filter_min_common", str(max(int(bark_hot_filter_min_common), 2)))

        device_keys = self._json_list_setting("bark_device_keys")
        if bark_device_keys is not None:
            device_keys = self._normalize_uid_list(bark_device_keys)
        if bark_add_device_key is not None and bark_add_device_key.strip():
            device_key = bark_add_device_key.strip()
            if device_key not in device_keys:
                device_keys.append(device_key)
        if bark_remove_device_key is not None and bark_remove_device_key.strip():
            remove_key = bark_remove_device_key.strip()
            device_keys = [device_key for device_key in device_keys if device_key != remove_key]
        self.set_app_setting(
            "bark_device_keys",
            json.dumps(self._normalize_uid_list(device_keys), ensure_ascii=False),
        )
        return self.get_bark_settings()

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

    def _clean_url(self, value: str) -> str:
        url = str(value or "").strip().rstrip("/")
        if not url:
            return ""
        if not (url.startswith("http://") or url.startswith("https://")):
            return "https://%s" % url
        return url

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
