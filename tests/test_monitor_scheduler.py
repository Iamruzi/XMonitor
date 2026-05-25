from __future__ import annotations

from twitter_monitor.scheduler import PollSchedule, poll_result_failed


def test_poll_schedule_uses_normal_random_range() -> None:
    seen_bounds = []

    def fake_rand(low: int, high: int) -> int:
        seen_bounds.append((low, high))
        return high

    schedule = PollSchedule(180, 300, 1800, rand_int=fake_rand)

    assert schedule.next_delay() == 300
    assert seen_bounds == [(180, 300)]


def test_poll_schedule_backs_off_after_failures_and_resets() -> None:
    seen_bounds = []

    def fake_rand(low: int, high: int) -> int:
        seen_bounds.append((low, high))
        return high

    schedule = PollSchedule(180, 300, 1800, rand_int=fake_rand)

    schedule.record_failure()
    assert schedule.next_delay() == 600
    schedule.record_failure()
    assert schedule.next_delay() == 1200
    schedule.record_failure()
    assert schedule.next_delay() == 1800
    schedule.record_success()
    assert schedule.next_delay() == 300

    assert seen_bounds == [(300, 600), (600, 1200), (1200, 1800), (180, 300)]


def test_poll_result_failed_detects_target_errors() -> None:
    assert poll_result_failed({"results": [{"handle": "ok"}, {"handle": "bad", "error": "limited"}]})
    assert not poll_result_failed({"results": [{"handle": "ok"}]})
