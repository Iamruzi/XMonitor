"""Notification adapters for monitor events."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import MonitorSettings
from .translator import LibreTranslateClient


@dataclass
class NotificationResult:
    sent: bool
    error: str | None = None


def _clip(text: str, limit: int = 900) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


class EventFormatter:
    _MENTION_OR_URL_RE = re.compile(r"https?://[^\s<]+|(?<![\w/])@([A-Za-z0-9_]{1,15})")
    _HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

    def __init__(self, proxy: str = "") -> None:
        self.proxy = proxy

    def format_html(self, event: dict[str, Any]) -> str:
        target = self._target_identity(event)
        title = self._linkify_plain_text(str(event.get("title") or "X monitor event"))
        body = self._linkify_plain_text(str(event.get("body") or ""))
        url = self._html_link(str(event.get("url") or ""), str(event.get("url") or ""))
        event_type = event.get("event_type")
        payload = self._payload(event)
        translated = self._translate(str(event.get("body") or ""))
        time_text = html.escape(self._format_time_plain(str(event.get("detected_at") or "")))
        if event_type == "tweet":
            lines = [
                "<b>%s 于 %s 原创发推</b>" % (target, time_text),
                "",
                "原文：",
                body,
            ]
        elif event_type == "retweet":
            lines = [
                "<b>%s 于 %s 转推了新内容</b>" % (target, time_text),
                "",
                "原文：",
                title,
                body,
            ]
        elif event_type == "reply":
            lines = [
                "<b>%s 于 %s 发表了回复</b>" % (target, time_text),
                "",
                "原文：",
                title,
                body,
            ]
        elif event_type == "following":
            followed = self._profile_link(str(payload.get("name") or event.get("title") or ""), str(payload.get("screenName") or ""))
            lines = [
                "<b>%s 于 %s 关注了 %s</b>" % (target, time_text, followed),
                "",
                "原简介：",
                body,
            ]
        else:
            lines = [
                "<b>%s</b>" % title,
                "发现时间：%s" % html.escape(self._format_time(str(event.get("detected_at") or ""))),
                body,
            ]
        if translated:
            lines.extend(["", "翻译简介：", self._linkify_plain_text(translated)])
        if url:
            lines.extend(["", "链接：%s" % url])
        return "\n".join(line for line in lines if line is not None)

    def format_wxpusher_html(self, event: dict[str, Any]) -> str:
        event_type = event.get("event_type")
        payload = self._payload(event)
        target = self._target_identity(event)
        time_text = html.escape(self._format_time_plain(str(event.get("detected_at") or "")))
        body = self._linkify_plain_text(str(event.get("body") or ""))
        title = self._linkify_plain_text(str(event.get("title") or "X monitor event"))
        translated = self._translate(str(event.get("body") or ""))
        url = str(event.get("url") or "")

        if event_type == "tweet":
            heading = "%s 于 %s 原创发推" % (target, time_text)
            sections = [self._section("原文", body)]
            sections.extend(self._tweet_profile_sections(payload))
        elif event_type == "retweet":
            heading = "%s 于 %s 转推了新内容" % (target, time_text)
            sections = [self._section("原文", "%s<br>%s" % (title, body))]
            sections.extend(self._tweet_profile_sections(payload))
        elif event_type == "reply":
            heading = "%s 于 %s 发表了回复" % (target, time_text)
            sections = [self._section("原文", "%s<br>%s" % (title, body))]
            sections.extend(self._tweet_profile_sections(payload))
        elif event_type == "following":
            followed = self._profile_link(
                str(payload.get("name") or event.get("title") or ""),
                str(payload.get("screenName") or ""),
            )
            heading = "%s 于 %s 关注了 %s" % (target, time_text, followed)
            sections = [self._section("原简介", body)]
        else:
            heading = title
            sections = [self._section("发现时间", html.escape(self._format_time(str(event.get("detected_at") or ""))))]
            if body:
                sections.append(self._section("内容", body))

        if translated:
            sections.append(self._section("翻译简介", self._linkify_plain_text(translated)))
        if url:
            sections.append(self._section("链接", self._html_link(url, url)))
        return "\n".join(
            [
                "<article>",
                "<h3>%s</h3>" % heading,
                *sections,
                "</article>",
            ]
        )

    def format_text(self, event: dict[str, Any]) -> str:
        without_tags = re.sub(r"<[^>]+>", "", self.format_html(event))
        return html.unescape(without_tags)

    def summary(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("event_type") or "")
        labels = {
            "tweet": "发推",
            "retweet": "转推",
            "reply": "回复",
            "following": "新增关注",
            "test": "测试通知",
        }
        return _clip("%s %s" % (self._target_identity_plain(event), labels.get(event_type, "新事件")), 64)

    def _payload(self, event: dict[str, Any]) -> dict[str, Any]:
        raw = event.get("payload_json") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _target_link(self, event: dict[str, Any]) -> str:
        handle = str(event.get("target_handle") or "unknown")
        if event.get("event_type") == "test":
            return html.escape("@%s" % handle)
        return self._profile_link(str(event.get("target_name") or ""), handle)

    def _target_identity(self, event: dict[str, Any]) -> str:
        if event.get("event_type") == "test":
            return self._target_link(event)
        group = str(event.get("target_group_name") or "").strip()
        remark = str(event.get("target_remark_name") or "").strip()
        parts = []
        if group:
            parts.append("【%s】" % html.escape(group))
        if remark:
            parts.append("%s ·" % html.escape(remark))
        parts.append(self._target_link(event))
        return " ".join(parts)

    def _target_identity_plain(self, event: dict[str, Any]) -> str:
        if event.get("event_type") == "test":
            return str(event.get("target_handle") or "monitor")
        group = str(event.get("target_group_name") or "").strip()
        remark = str(event.get("target_remark_name") or "").strip()
        handle = str(event.get("target_handle") or "unknown").lstrip("@")
        display_name = str(event.get("target_name") or "").strip()
        profile = "@%s" % handle
        if display_name and display_name != handle and display_name != "@%s" % handle:
            profile = "%s（@%s）" % (display_name, handle)
        parts = []
        if group:
            parts.append(group)
        if remark:
            parts.append(remark)
        parts.append(profile)
        return "｜".join(parts)

    def _tweet_profile_sections(self, payload: dict[str, Any]) -> list[str]:
        sections = []
        author = payload.get("author") or {}
        if isinstance(author, dict) and author.get("screenName"):
            sections.append(
                self._section(
                    "推文作者",
                    self._profile_link(str(author.get("name") or ""), str(author.get("screenName") or "")),
                )
            )
        retweeted_by = str(payload.get("retweetedBy") or "")
        if retweeted_by:
            sections.append(self._section("转推用户", self._profile_link("", retweeted_by)))
        in_reply_to = str(payload.get("inReplyToScreenName") or "")
        if in_reply_to:
            sections.append(self._section("回复对象", self._profile_link("", in_reply_to)))
        quoted = payload.get("quotedTweet") or {}
        if isinstance(quoted, dict):
            quoted_author = quoted.get("author") or {}
            if isinstance(quoted_author, dict) and quoted_author.get("screenName"):
                sections.append(
                    self._section(
                        "引用用户",
                        self._profile_link(
                            str(quoted_author.get("name") or ""),
                            str(quoted_author.get("screenName") or ""),
                        ),
                    )
                )
        return sections

    def _section(self, label: str, content: str) -> str:
        if not content:
            return ""
        return "<p><strong>%s：</strong><br>%s</p>" % (html.escape(label), content)

    def _profile_link(self, display_name: str, handle: str) -> str:
        cleaned = self._clean_handle(handle)
        if not cleaned:
            return self._linkify_plain_text(display_name)
        display = display_name.strip()
        label = "@%s" % cleaned
        if display and display != cleaned and display != "@%s" % cleaned:
            label = "%s（@%s）" % (display, cleaned)
        return self._html_link(self._profile_url(cleaned), label)

    def _profile_url(self, handle: str) -> str:
        return "https://x.com/%s" % self._clean_handle(handle)

    def _clean_handle(self, handle: str) -> str:
        cleaned = str(handle or "").strip().lstrip("@")
        if not self._HANDLE_RE.fullmatch(cleaned):
            return ""
        return cleaned

    def _linkify_plain_text(self, text: str, limit: int = 900) -> str:
        raw = _clip(text, limit)
        parts = []
        last = 0
        for match in self._MENTION_OR_URL_RE.finditer(raw):
            parts.append(html.escape(raw[last:match.start()]))
            matched = match.group(0)
            handle = match.group(1)
            if handle:
                parts.append(self._profile_link("", handle))
            else:
                parts.append(self._html_link(matched, matched))
            last = match.end()
        parts.append(html.escape(raw[last:]))
        return "".join(parts)

    def _html_link(self, url: str, label: str) -> str:
        clean_url = str(url or "").strip()
        if not clean_url:
            return html.escape(str(label or ""))
        return '<a href="%s">%s</a>' % (
            html.escape(clean_url, quote=True),
            html.escape(str(label or clean_url)),
        )

    def _translate(self, text: str) -> str:
        return LibreTranslateClient(proxy=self.proxy).translate_to_chinese(text)

    def _format_time(self, value: str) -> str:
        formatted, timezone_name = self._format_time_parts(value)
        if not timezone_name:
            return formatted
        return "%s（%s）" % (formatted, timezone_name)

    def _format_time_plain(self, value: str) -> str:
        formatted, _timezone_name = self._format_time_parts(value)
        return formatted

    def _format_time_parts(self, value: str) -> tuple[str, str]:
        if not value:
            return "未知", ""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value, ""
        timezone_name = os.environ.get("MONITOR_TIMEZONE", "Asia/Shanghai")
        tz: tzinfo
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name == "Asia/Shanghai":
                tz = timezone(timedelta(hours=8))
                timezone_name = "Asia/Shanghai"
            else:
                tz = timezone.utc
                timezone_name = "UTC"
        local = dt.astimezone(tz)
        return local.strftime("%Y-%m-%d %H:%M:%S"), timezone_name


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str | list[str], proxy: str = "") -> None:
        self.bot_token = bot_token
        self.chat_ids = self._normalize_chat_ids(chat_id)
        self.proxy = proxy
        self.formatter = EventFormatter(proxy)

    @classmethod
    def from_settings(cls, settings: MonitorSettings) -> "TelegramNotifier":
        return cls(settings.telegram_bot_token, settings.telegram_chat_id, settings.telegram_proxy)

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_ids)

    def send_event(self, event: dict[str, Any]) -> NotificationResult:
        if not self.configured:
            return NotificationResult(False, "telegram_not_configured")
        text = self._format_event(event)
        sent = False
        errors = []
        opener = self._opener()
        for chat_id in self.chat_ids:
            payload = json.dumps(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % self.bot_token,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with opener.open(request, timeout=15) as response:
                    if response.status >= 400:
                        errors.append("telegram_%s_http_%d" % (chat_id, response.status))
                    else:
                        sent = True
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                errors.append("telegram_%s_http_%d: %s" % (chat_id, exc.code, detail))
            except Exception as exc:
                errors.append("telegram_%s_%s: %s" % (chat_id, type(exc).__name__, exc))
        if sent:
            return NotificationResult(True, "; ".join(errors) or None)
        return NotificationResult(False, "; ".join(errors) or "telegram_send_failed")

    def _normalize_chat_ids(self, chat_id: str | list[str]) -> list[str]:
        values = chat_id if isinstance(chat_id, list) else [chat_id]
        result = []
        seen = set()
        for value in values:
            item = str(value or "").strip()
            if not item or item in seen:
                continue
            result.append(item)
            seen.add(item)
        return result

    def send_message(self, chat_id: str, text: str) -> NotificationResult:
        if not self.bot_token or not chat_id:
            return NotificationResult(False, "telegram_not_configured")
        payload = json.dumps(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % self.bot_token,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = self._opener()
            with opener.open(request, timeout=15) as response:
                if response.status >= 400:
                    return NotificationResult(False, "telegram_http_%d" % response.status)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            return NotificationResult(False, "telegram_http_%d: %s" % (exc.code, detail))
        except Exception as exc:
            return NotificationResult(False, "%s: %s" % (type(exc).__name__, exc))
        return NotificationResult(True)

    def _opener(self) -> urllib.request.OpenerDirector:
        if not self.proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
        )

    def _format_event(self, event: dict[str, Any]) -> str:
        return self.formatter.format_html(event)


class WxPusherNotifier:
    endpoint = "https://wxpusher.zjiecode.com/api/send/message"

    def __init__(self, app_token: str, uids: list[str], proxy: str = "") -> None:
        self.app_token = app_token
        self.uids = [uid for uid in uids if uid]
        self.proxy = proxy
        self.formatter = EventFormatter(proxy)

    @property
    def configured(self) -> bool:
        return bool(self.app_token and self.uids)

    def send_event(self, event: dict[str, Any]) -> NotificationResult:
        if not self.configured:
            return NotificationResult(False, "wxpusher_not_configured")
        payload = json.dumps(
            {
                "appToken": self.app_token,
                "content": self.formatter.format_wxpusher_html(event),
                "summary": self.formatter.summary(event),
                "contentType": 2,
                "uids": self.uids,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = self._opener()
            with opener.open(request, timeout=15) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status >= 400:
                    return NotificationResult(False, "wxpusher_http_%d: %s" % (response.status, body[:200]))
                try:
                    data = json.loads(body)
                except ValueError:
                    data = {}
                if isinstance(data, dict) and data.get("code") not in (None, 1000):
                    return NotificationResult(False, "wxpusher_%s: %s" % (data.get("code"), data.get("msg", "")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            return NotificationResult(False, "wxpusher_http_%d: %s" % (exc.code, detail))
        except Exception as exc:
            return NotificationResult(False, "%s: %s" % (type(exc).__name__, exc))
        return NotificationResult(True)

    def _opener(self) -> urllib.request.OpenerDirector:
        if not self.proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
        )


class BarkNotifier:
    def __init__(
        self,
        server_url: str,
        device_keys: list[str],
        *,
        level: str = "active",
        sound: str = "",
        group: str = "XMonitor",
        call: bool = False,
        volume: int = 5,
        proxy: str = "",
    ) -> None:
        self.server_url = (server_url or "https://api.day.app").strip().rstrip("/")
        self.device_keys = [key for key in device_keys if key]
        self.level = self._normalize_level(level)
        self.sound = sound.strip()
        self.group = group.strip() or "XMonitor"
        self.call = call
        self.volume = min(max(int(volume), 0), 10)
        self.proxy = proxy
        self.formatter = EventFormatter(proxy)

    @property
    def configured(self) -> bool:
        return bool(self.server_url and self.device_keys)

    def send_event(self, event: dict[str, Any]) -> NotificationResult:
        if not self.configured:
            return NotificationResult(False, "bark_not_configured")
        effective_level = "critical" if self.call else self.level
        payload = {
            "title": self.formatter.summary(event),
            "body": _clip(self.formatter.format_text(event), 1800),
            "group": self.group,
            "level": effective_level,
            "isArchive": "1",
        }  # type: dict[str, Any]
        if len(self.device_keys) == 1:
            payload["device_key"] = self.device_keys[0]
        else:
            payload["device_keys"] = self.device_keys
        if event.get("url"):
            payload["url"] = str(event.get("url") or "")
        if self.sound:
            payload["sound"] = self.sound
        if self.call:
            payload["call"] = "1"
        if effective_level == "critical":
            payload["volume"] = str(self.volume)
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = self._opener()
            with opener.open(request, timeout=15) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status >= 400:
                    return NotificationResult(False, "bark_http_%d: %s" % (response.status, body[:200]))
                try:
                    data = json.loads(body)
                except ValueError:
                    data = {}
                code = data.get("code") if isinstance(data, dict) else None
                if code not in (None, 200, 1000):
                    return NotificationResult(False, "bark_%s: %s" % (code, data.get("message", "")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            return NotificationResult(False, "bark_http_%d: %s" % (exc.code, detail))
        except Exception as exc:
            return NotificationResult(False, "%s: %s" % (type(exc).__name__, exc))
        return NotificationResult(True)

    def _endpoint(self) -> str:
        if self.server_url.endswith("/push"):
            return self.server_url
        return "%s/push" % self.server_url

    def _opener(self) -> urllib.request.OpenerDirector:
        if not self.proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
        )

    def _normalize_level(self, level: str) -> str:
        normalized = str(level or "").strip()
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


class CompositeNotifier:
    def __init__(self, adapters: list[Any]) -> None:
        self.adapters = adapters

    @property
    def configured(self) -> bool:
        return any(getattr(adapter, "configured", False) for adapter in self.adapters)

    def send_event(self, event: dict[str, Any]) -> NotificationResult:
        if not self.adapters:
            return NotificationResult(False, "notification_not_configured")
        sent = False
        errors = []
        for adapter in self.adapters:
            if not getattr(adapter, "configured", False):
                continue
            result = adapter.send_event(event)
            sent = sent or result.sent
            if not result.sent and result.error:
                errors.append(result.error)
        if sent:
            return NotificationResult(True, "; ".join(errors) or None)
        return NotificationResult(False, "; ".join(errors) or "notification_not_configured")
