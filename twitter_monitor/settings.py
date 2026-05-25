"""Runtime settings for the Twitter/X monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        parsed = default
    return max(parsed, minimum)


@dataclass(frozen=True)
class MonitorSettings:
    db_path: str
    poll_interval_seconds: int
    poll_interval_min_seconds: int
    poll_interval_max_seconds: int
    poll_backoff_max_seconds: int
    background_worker: bool
    default_tweet_fetch_count: int
    default_following_fetch_count: int
    admin_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_proxy: str
    wxpusher_app_token: str
    wxpusher_uids: str
    telegram_commands_enabled: bool

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def wxpusher_configured(self) -> bool:
        return bool(self.wxpusher_app_token and self.wxpusher_uids)

    @property
    def admin_required(self) -> bool:
        return bool(self.admin_token)


def load_settings() -> MonitorSettings:
    legacy_interval = _as_int(os.environ.get("MONITOR_POLL_INTERVAL"), 300, minimum=30)
    has_range = bool(os.environ.get("MONITOR_POLL_INTERVAL_MIN") or os.environ.get("MONITOR_POLL_INTERVAL_MAX"))
    default_min = 180 if has_range or not os.environ.get("MONITOR_POLL_INTERVAL") else legacy_interval
    default_max = 300 if has_range or not os.environ.get("MONITOR_POLL_INTERVAL") else legacy_interval
    poll_min = _as_int(os.environ.get("MONITOR_POLL_INTERVAL_MIN"), default_min, minimum=30)
    poll_max = _as_int(os.environ.get("MONITOR_POLL_INTERVAL_MAX"), default_max, minimum=poll_min)
    poll_backoff_max = _as_int(os.environ.get("MONITOR_POLL_BACKOFF_MAX"), 1800, minimum=poll_max)
    return MonitorSettings(
        db_path=os.environ.get("MONITOR_DB_PATH", "twitter-monitor.db"),
        poll_interval_seconds=poll_max,
        poll_interval_min_seconds=poll_min,
        poll_interval_max_seconds=poll_max,
        poll_backoff_max_seconds=poll_backoff_max,
        background_worker=_as_bool(os.environ.get("MONITOR_BACKGROUND_WORKER"), True),
        default_tweet_fetch_count=_as_int(os.environ.get("MONITOR_TWEET_FETCH_COUNT"), 10),
        default_following_fetch_count=_as_int(os.environ.get("MONITOR_FOLLOWING_FETCH_COUNT"), 40),
        admin_token=os.environ.get("MONITOR_ADMIN_TOKEN", "Vip.123456"),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        telegram_proxy=os.environ.get("TELEGRAM_PROXY", "") or os.environ.get("TWITTER_PROXY", ""),
        wxpusher_app_token=os.environ.get("WXPUSHER_APP_TOKEN", ""),
        wxpusher_uids=os.environ.get("WXPUSHER_UIDS", ""),
        telegram_commands_enabled=_as_bool(os.environ.get("MONITOR_TG_COMMANDS"), True),
    )
