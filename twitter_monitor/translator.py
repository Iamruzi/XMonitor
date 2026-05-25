"""Optional translation helpers for notification text."""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def should_translate_to_chinese(text: str) -> bool:
    cleaned = " ".join((text or "").split())
    if len(cleaned) < 4:
        return False
    if _has_cjk(cleaned):
        return False
    return any(char.isalpha() for char in cleaned)


class LibreTranslateClient:
    def __init__(self, *, proxy: str = "") -> None:
        self.enabled = _env_bool("MONITOR_TRANSLATE_ENABLED", True)
        self.endpoint = os.environ.get("MONITOR_TRANSLATE_URL", "https://libretranslate.com/translate")
        self.api_key = os.environ.get("MONITOR_TRANSLATE_API_KEY", "")
        self.target = os.environ.get("MONITOR_TRANSLATE_TARGET", "zh")
        self.google_api_key = os.environ.get("MONITOR_GOOGLE_TRANSLATE_API_KEY", "")
        self.google_target = os.environ.get("MONITOR_GOOGLE_TRANSLATE_TARGET", "zh-CN")
        self.mymemory_enabled = _env_bool("MONITOR_MYMEMORY_ENABLED", True)
        self.mymemory_source = os.environ.get("MONITOR_MYMEMORY_SOURCE", "en")
        self.mymemory_target = os.environ.get("MONITOR_MYMEMORY_TARGET", "zh-CN")
        self.mymemory_email = os.environ.get("MONITOR_MYMEMORY_EMAIL", "")
        self.proxy = os.environ.get("MONITOR_TRANSLATE_PROXY", "") or proxy

    def translate_to_chinese(self, text: str) -> str:
        if not self.enabled or not should_translate_to_chinese(text):
            return ""
        translated = self._translate_with_google(text)
        if translated:
            return translated
        translated = self._translate_with_libretranslate(text)
        if translated:
            return translated
        if self.mymemory_enabled:
            return self._translate_with_mymemory(text)
        return ""

    def _translate_with_google(self, text: str) -> str:
        if not self.google_api_key:
            return ""
        url = "https://translation.googleapis.com/language/translate/v2?%s" % urllib.parse.urlencode(
            {"key": self.google_api_key}
        )
        payload = {
            "q": text,
            "target": self.google_target,
            "format": "text",
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener().open(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.info("Google Cloud Translation failed: %s", exc)
            return ""
        translations = data.get("data", {}).get("translations", []) if isinstance(data, dict) else []
        if not translations:
            return ""
        translated = html.unescape(str(translations[0].get("translatedText") or "")).strip()
        if not translated or translated == text.strip():
            return ""
        return translated

    def _translate_with_libretranslate(self, text: str) -> str:
        payload = {
            "q": text,
            "source": "auto",
            "target": self.target,
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener().open(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.info("LibreTranslate failed: %s", exc)
            return ""
        translated = str(data.get("translatedText") or "").strip()
        if not translated or translated == text.strip():
            return ""
        return translated

    def _translate_with_mymemory(self, text: str) -> str:
        params = {
            "q": text,
            "langpair": "%s|%s" % (self.mymemory_source, self.mymemory_target),
            "mt": "1",
        }
        if self.mymemory_email:
            params["de"] = self.mymemory_email
        url = "https://api.mymemory.translated.net/get?%s" % urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._opener().open(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.info("MyMemory fallback failed: %s", exc)
            return ""
        response_data = data.get("responseData") if isinstance(data, dict) else {}
        if not isinstance(response_data, dict):
            return ""
        translated = html.unescape(str(response_data.get("translatedText") or "")).strip()
        if not translated or translated == text.strip():
            return ""
        return translated

    def _opener(self) -> urllib.request.OpenerDirector:
        if not self.proxy:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
        )
