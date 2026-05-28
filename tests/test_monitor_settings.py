from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException

from twitter_monitor import app as monitor_app
from twitter_monitor.app import _admin_token_matches, require_admin
from twitter_monitor.settings import load_settings


def test_admin_token_has_no_builtin_default(monkeypatch) -> None:
    monkeypatch.delenv("MONITOR_ADMIN_TOKEN", raising=False)

    settings = load_settings()

    assert settings.admin_token == ""
    assert settings.admin_required is True
    assert settings.admin_configured is False


def test_initial_following_fetch_count_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_INITIAL_FOLLOWING_FETCH_COUNT", "150")

    settings = load_settings()

    assert settings.default_initial_following_fetch_count == 150


def test_admin_token_match_accepts_percent_encoded_unicode() -> None:
    assert _admin_token_matches("plain-token", "plain-token") is True
    assert _admin_token_matches("%E4%B8%AD%E6%96%87%E5%AF%86%E7%A0%81", "中文密码") is True
    assert _admin_token_matches("%E9%94%99%E8%AF%AF", "正确密码") is False


def test_auth_check_validates_admin_token(monkeypatch) -> None:
    monkeypatch.setattr(
        monitor_app,
        "settings",
        replace(monitor_app.settings, admin_token="secret-token"),
    )

    require_admin("secret-token")
    with pytest.raises(HTTPException) as exc_info:
        require_admin("wrong-token")

    assert exc_info.value.status_code == 401


def test_auth_check_route_exists() -> None:
    assert "/api/auth/check" in {route.path for route in monitor_app.app.routes}
