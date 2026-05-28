"""Polling logic for monitored Twitter/X handles."""

from __future__ import annotations

import logging
import os
from typing import Any

from twitter_cli.client import TwitterClient, set_runtime_proxy
from twitter_cli.config import load_config
from twitter_cli.exceptions import TwitterAPIError
from twitter_cli.serialization import tweet_to_dict, user_profile_to_dict

from .notifiers import BarkNotifier, CompositeNotifier, TelegramNotifier, WxPusherNotifier
from .rate_limiter import XRequestLimiter
from .settings import MonitorSettings
from .storage import MonitorStorage

logger = logging.getLogger(__name__)


EVENT_LABELS = {
    "tweet": "原创发推",
    "retweet": "转推",
    "reply": "回复",
    "following": "新增关注",
}


class MonitorPoller:
    def __init__(
        self,
        storage: MonitorStorage,
        settings: MonitorSettings,
        notifier: TelegramNotifier,
    ) -> None:
        self.storage = storage
        self.settings = settings
        self.notifier = notifier
        self.request_limiter = XRequestLimiter(
            min_delay_seconds=settings.x_request_delay_min_seconds,
            max_delay_seconds=settings.x_request_delay_max_seconds,
        )

    def poll_all(self) -> dict[str, Any]:
        targets = [target for target in self.storage.list_targets() if target.get("enabled")]
        results = []
        for target in targets:
            results.append(self.poll_target(target))
        return {
            "targetsChecked": len(results),
            "results": results,
        }

    def poll_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = int(target["id"])
        handle = str(target["handle"])
        result = {
            "targetId": target_id,
            "handle": handle,
            "newTweets": 0,
            "newRetweets": 0,
            "newReplies": 0,
            "newFollowing": 0,
            "notificationsSent": 0,
            "notificationErrors": 0,
            "snapshotted": [],
        }  # type: dict[str, Any]
        try:
            client = self._make_client()
            profile = client.fetch_user(handle)
            self.storage.set_profile(
                target_id,
                user_id=profile.id,
                handle=profile.screen_name or handle,
                display_name=profile.name,
            )

            if target.get("monitor_tweets"):
                tweet_result = self._poll_tweets(client, target, profile.id)
                result.update(tweet_result)
            if target.get("monitor_following"):
                following_result = self._poll_following(client, target, profile.id)
                result["newFollowing"] = following_result["newFollowing"]
                result["notificationsSent"] += following_result["notificationsSent"]
                result["notificationErrors"] += following_result["notificationErrors"]
                result["snapshotted"].extend(following_result["snapshotted"])
            self.storage.set_checked(target_id, error=None)
        except Exception as exc:
            logger.exception("Failed to poll @%s", handle)
            self.storage.set_checked(target_id, error=str(exc))
            result["error"] = str(exc)
        return result

    def poll_target_task(self, target: dict[str, Any], task_type: str) -> dict[str, Any]:
        target_id = int(target["id"])
        handle = str(target["handle"])
        result = {
            "targetId": target_id,
            "handle": handle,
            "taskType": task_type,
            "newTweets": 0,
            "newRetweets": 0,
            "newReplies": 0,
            "newFollowing": 0,
            "notificationsSent": 0,
            "notificationErrors": 0,
            "snapshotted": [],
        }  # type: dict[str, Any]
        try:
            client = self._make_client()
            profile = client.fetch_user(handle)
            self.storage.set_profile(
                target_id,
                user_id=profile.id,
                handle=profile.screen_name or handle,
                display_name=profile.name,
            )
            if task_type == "tweets":
                if target.get("monitor_tweets"):
                    result.update(self._poll_tweets(client, target, profile.id))
            elif task_type == "following":
                if target.get("monitor_following"):
                    following_result = self._poll_following(client, target, profile.id)
                    result["newFollowing"] = following_result["newFollowing"]
                    result["notificationsSent"] += following_result["notificationsSent"]
                    result["notificationErrors"] += following_result["notificationErrors"]
                    result["snapshotted"].extend(following_result["snapshotted"])
            else:
                raise ValueError("未知轮询任务类型：%s" % task_type)
            self.storage.set_checked(target_id, error=None)
        except Exception as exc:
            logger.exception("Failed to run %s task for @%s", task_type, handle)
            self.storage.set_checked(target_id, error=str(exc))
            result["error"] = str(exc)
            result["errorCode"] = self._error_code(exc)
        return result

    def backfill_following(self, target: dict[str, Any], count: int | None = None) -> dict[str, Any]:
        target_id = int(target["id"])
        handle = str(target["handle"])
        result = {
            "targetId": target_id,
            "handle": handle,
            "countRequested": 0,
            "fetchedFollowing": 0,
            "backfilledFollowing": 0,
            "sharedMatches": 0,
            "projectMatches": 0,
        }  # type: dict[str, Any]
        try:
            client = self._make_client()
            profile = client.fetch_user(handle)
            self.storage.set_profile(
                target_id,
                user_id=profile.id,
                handle=profile.screen_name or handle,
                display_name=profile.name,
            )

            fetch_count = self._following_fetch_count(target, initial=True)
            if count is not None:
                fetch_count = max(fetch_count, int(count))
            users = client.fetch_following(profile.id, fetch_count)
            user_ids = [user.id for user in users if user.id]
            known_ids = self.storage.get_seen_following_ids(target_id, user_ids)
            new_ids = [user_id for user_id in user_ids if user_id not in known_ids]

            self.storage.upsert_followed_users(users)
            self.storage.add_seen_following(target_id, user_ids)
            self.storage.set_initialized(target_id, following=True)
            self.storage.set_checked(target_id, error=None)

            shared_matches = 0
            project_matches = 0
            for user_id in user_ids:
                context = self.storage.followed_account_context(str(user_id))
                if not context or int(context.get("commonCount") or 0) < 2:
                    continue
                shared_matches += 1
                project_matches += int(bool(context.get("isProject")))

            result.update(
                {
                    "handle": profile.screen_name or handle,
                    "countRequested": fetch_count,
                    "fetchedFollowing": len(user_ids),
                    "backfilledFollowing": len(new_ids),
                    "sharedMatches": shared_matches,
                    "projectMatches": project_matches,
                }
            )
        except Exception as exc:
            logger.exception("Failed to backfill following for @%s", handle)
            self.storage.set_checked(target_id, error=str(exc))
            result["error"] = str(exc)
        return result

    def _make_client(self) -> TwitterClient:
        auth_token = os.environ.get("TWITTER_AUTH_TOKEN", "")
        ct0 = os.environ.get("TWITTER_CT0", "")
        if not auth_token or not ct0:
            raise RuntimeError("TWITTER_AUTH_TOKEN and TWITTER_CT0 are required")
        set_runtime_proxy(self._network_proxy())
        config = load_config()
        return TwitterClient(auth_token, ct0, config.get("rateLimit"), request_limiter=self.request_limiter)

    def _network_proxy(self) -> str:
        db_settings = self.storage.get_notification_settings()
        return (db_settings.get("telegram_proxy") or self.settings.telegram_proxy or "").strip()

    def _poll_tweets(self, client: TwitterClient, target: dict[str, Any], user_id: str) -> dict[str, Any]:
        target_id = int(target["id"])
        count = int(target.get("tweet_fetch_count") or self.settings.default_tweet_fetch_count)
        tweets = client.fetch_user_tweets(user_id, count)
        tweet_ids = [tweet.id for tweet in tweets if tweet.id]
        known_ids = self.storage.get_seen_tweet_ids(target_id, tweet_ids)
        self.storage.add_seen_tweets(target_id, tweet_ids)

        if not target.get("tweets_initialized"):
            self.storage.set_initialized(target_id, tweets=True)
            return {
                "newTweets": 0,
                "notificationsSent": 0,
                "notificationErrors": 0,
                "snapshotted": ["tweets"],
            }

        new_tweets = [
            tweet for tweet in tweets
            if tweet.id and tweet.id not in known_ids and self._event_enabled(target, self._tweet_event_type(tweet))
        ]
        sent = 0
        errors = 0
        counts = {"tweet": 0, "retweet": 0, "reply": 0}
        for tweet in reversed(new_tweets):
            event_type = self._tweet_event_type(tweet)
            counts[event_type] += 1
            title = self._tweet_title(event_type, tweet)
            event = self.storage.create_event(
                target_id=target_id,
                event_type=event_type,
                external_id=tweet.id,
                title=title,
                body=tweet.text,
                url="https://x.com/%s/status/%s" % (tweet.author.screen_name, tweet.id),
                payload=tweet_to_dict(tweet),
            )
            if event:
                outcome = self._notify(event)
                sent += int(outcome["sent"])
                errors += int(not outcome["sent"])
        return {
            "newTweets": counts["tweet"],
            "newRetweets": counts["retweet"],
            "newReplies": counts["reply"],
            "notificationsSent": sent,
            "notificationErrors": errors,
            "snapshotted": [],
        }

    def _tweet_event_type(self, tweet: Any) -> str:
        if getattr(tweet, "is_retweet", False):
            return "retweet"
        if getattr(tweet, "in_reply_to_status_id", None) or getattr(tweet, "in_reply_to_screen_name", None):
            return "reply"
        return "tweet"

    def _event_enabled(self, target: dict[str, Any], event_type: str) -> bool:
        if event_type == "tweet":
            return bool(target.get("monitor_tweets"))
        if event_type == "retweet":
            return bool(target.get("monitor_retweets"))
        if event_type == "reply":
            return bool(target.get("monitor_replies"))
        return True

    def _tweet_title(self, event_type: str, tweet: Any) -> str:
        label = EVENT_LABELS.get(event_type, "动态")
        if event_type == "retweet" and getattr(tweet, "retweeted_by", None):
            return "%s：@%s 转推了 @%s" % (label, tweet.retweeted_by, tweet.author.screen_name)
        if event_type == "reply" and getattr(tweet, "in_reply_to_screen_name", None):
            return "%s：@%s 回复了 @%s" % (label, tweet.author.screen_name, tweet.in_reply_to_screen_name)
        return "%s：@%s" % (label, tweet.author.screen_name)

    def _poll_following(self, client: TwitterClient, target: dict[str, Any], user_id: str) -> dict[str, Any]:
        target_id = int(target["id"])
        count = self._following_fetch_count(target, initial=not target.get("following_initialized"))
        users = client.fetch_following(user_id, count)
        user_ids = [user.id for user in users if user.id]
        known_ids = self.storage.get_seen_following_ids(target_id, user_ids)
        self.storage.upsert_followed_users(users)
        self.storage.add_seen_following(target_id, user_ids)

        if not target.get("following_initialized"):
            self.storage.set_initialized(target_id, following=True)
            return {
                "newFollowing": 0,
                "notificationsSent": 0,
                "notificationErrors": 0,
                "snapshotted": ["following"],
            }

        new_users = [user for user in users if user.id and user.id not in known_ids]
        sent = 0
        errors = 0
        for user in reversed(new_users):
            payload = user_profile_to_dict(user)
            hot_context = self.storage.followed_account_context(str(user.id))
            if hot_context and hot_context.get("isProject") and int(hot_context.get("commonCount") or 0) >= 2:
                payload["hotProject"] = self._hot_project_payload(hot_context)
            event = self.storage.create_event(
                target_id=target_id,
                event_type="following",
                external_id=user.id,
                title="%s（@%s）" % (user.name, user.screen_name),
                body=user.bio,
                url="https://x.com/%s" % user.screen_name,
                payload=payload,
            )
            if event:
                outcome = self._notify(event)
                sent += int(outcome["sent"])
                errors += int(not outcome["sent"])
        return {
            "newFollowing": len(new_users),
            "notificationsSent": sent,
            "notificationErrors": errors,
            "snapshotted": [],
        }

    def _following_fetch_count(self, target: dict[str, Any], *, initial: bool) -> int:
        count = int(target.get("following_fetch_count") or self.settings.default_following_fetch_count)
        if initial:
            count = max(count, self.settings.default_initial_following_fetch_count)
        return count

    def _notify(self, event: dict[str, Any]) -> dict[str, Any]:
        outcome = self._notification_adapter().send_event(event)
        self.storage.mark_event_notified(
            int(event["id"]),
            error=None if outcome.sent else outcome.error,
        )
        return {"sent": outcome.sent, "error": outcome.error}

    def _hot_project_payload(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "userId": str(account.get("userId") or ""),
            "handle": str(account.get("handle") or ""),
            "name": str(account.get("name") or ""),
            "category": str(account.get("category") or ""),
            "commonCount": int(account.get("commonCount") or 0),
            "earlyScore": int(account.get("earlyScore") or 0),
            "followerStage": str(account.get("followerStage") or ""),
            "discoverySignals": list(account.get("discoverySignals") or []),
            "latestTrendText": str(account.get("latestTrendText") or ""),
            "trendEvents": list(account.get("trendEvents") or []),
        }

    def _notification_adapter(self, channel: str = "all") -> CompositeNotifier:
        channel = self._normalize_channel(channel)
        db_settings = self.storage.get_notification_settings()
        db_token = db_settings.get("telegram_bot_token") or ""
        db_chat_id = db_settings.get("telegram_chat_id") or ""
        telegram = self.notifier
        token = db_token or self.settings.telegram_bot_token
        chat_id = db_chat_id or self.settings.telegram_chat_id
        if token and chat_id:
            chat_ids = [chat_id, *self.storage.get_telegram_authorized_chat_ids()]
            telegram = TelegramNotifier(
                token,
                chat_ids,
                db_settings.get("telegram_proxy") or self.settings.telegram_proxy,
            )

        wx_settings = self.storage.get_wxpusher_settings()
        wx_app_token = wx_settings.get("wxpusher_app_token") or self.settings.wxpusher_app_token
        wx_uids = wx_settings.get("wxpusher_uids") or self._split_uids(self.settings.wxpusher_uids)
        wxpusher = (
            WxPusherNotifier(
                str(wx_app_token or ""),
                [str(uid) for uid in wx_uids],
                db_settings.get("telegram_proxy") or self.settings.telegram_proxy,
                hot_filter_enabled=self._setting_bool(wx_settings.get("wxpusher_hot_filter_enabled"), False),
                hot_filter_min_common=self._setting_int(wx_settings.get("wxpusher_hot_filter_min_common"), 2),
            )
            if self._setting_bool(wx_settings.get("wxpusher_enabled"), True)
            else None
        )
        bark_settings = self.storage.get_bark_settings()
        bark_keys = bark_settings.get("bark_device_keys") or self._split_uids(self.settings.bark_device_keys)
        bark = (
            BarkNotifier(
                str(bark_settings.get("bark_server_url") or self.settings.bark_server_url),
                [str(key) for key in bark_keys],
                level=str(bark_settings.get("bark_level") or self.settings.bark_level),
                sound=str(bark_settings.get("bark_sound") or self.settings.bark_sound),
                group=str(bark_settings.get("bark_group") or self.settings.bark_group),
                call=self._setting_bool(bark_settings.get("bark_call"), self.settings.bark_call),
                volume=self._setting_int(bark_settings.get("bark_volume"), self.settings.bark_volume),
                proxy=db_settings.get("telegram_proxy") or self.settings.telegram_proxy,
                hot_filter_enabled=self._setting_bool(bark_settings.get("bark_hot_filter_enabled"), False),
                hot_filter_min_common=self._setting_int(bark_settings.get("bark_hot_filter_min_common"), 2),
            )
            if self._setting_bool(bark_settings.get("bark_enabled"), True)
            else None
        )
        adapters: list[Any] = []
        if channel in {"all", "telegram"}:
            adapters.append(telegram)
        if channel in {"all", "wxpusher"} and wxpusher is not None:
            adapters.append(wxpusher)
        if channel in {"all", "bark"} and bark is not None:
            adapters.append(bark)
        return CompositeNotifier(adapters)

    def _normalize_channel(self, channel: str) -> str:
        normalized = str(channel or "all").strip().lower()
        aliases = {
            "all": "all",
            "全部": "all",
            "telegram": "telegram",
            "tg": "telegram",
            "wxpusher": "wxpusher",
            "wx": "wxpusher",
            "bark": "bark",
        }
        if normalized not in aliases:
            raise ValueError("未知通知渠道：%s" % channel)
        return aliases[normalized]

    def _split_uids(self, raw: str) -> list[str]:
        return [uid.strip() for uid in raw.replace(";", ",").split(",") if uid.strip()]

    def _setting_bool(self, raw: Any, default: bool) -> bool:
        if raw in (None, ""):
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "开", "开启"}

    def _setting_int(self, raw: Any, default: int) -> int:
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return default

    def _error_code(self, exc: Exception) -> str:
        if isinstance(exc, TwitterAPIError):
            return exc.error_code
        return getattr(exc, "error_code", "api_error")
