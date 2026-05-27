from __future__ import annotations

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
