from __future__ import annotations

from twitter_monitor.storage import MonitorStorage, utc_after


def test_poll_tasks_sync_acquire_and_complete(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.add_target("alice")

    status = storage.sync_poll_tasks(tweet_interval_seconds=600, following_interval_seconds=1800)

    assert status["summary"]["total"] == 2
    assert status["summary"]["due"] == 2

    tasks = storage.acquire_due_poll_tasks(limit=1, lease_seconds=60)

    assert len(tasks) == 1
    assert tasks[0]["status"] == "running"
    assert tasks[0]["target"]["handle"] == "alice"

    storage.complete_poll_task(
        int(tasks[0]["id"]),
        success=True,
        next_run_after=utc_after(600),
    )
    refreshed = storage.poll_queue_status()
    targets = storage.list_targets()

    assert refreshed["summary"]["running"] == 0
    assert refreshed["summary"]["total"] == 2
    assert len(targets[0]["pollTasks"]) == 2


def test_poll_tasks_remove_disabled_targets(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    target = storage.add_target("alice")
    storage.sync_poll_tasks(tweet_interval_seconds=600, following_interval_seconds=1800)

    storage.update_target(int(target["id"]), {"enabled": False})
    status = storage.sync_poll_tasks(tweet_interval_seconds=600, following_interval_seconds=1800)

    assert status["summary"]["total"] == 0


def test_poll_task_failure_records_backoff_state(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    storage.add_target("alice", monitor_following=False)
    storage.sync_poll_tasks(tweet_interval_seconds=600, following_interval_seconds=1800)
    task = storage.acquire_due_poll_tasks(limit=1)[0]

    storage.complete_poll_task(
        int(task["id"]),
        success=False,
        next_run_after=utc_after(300),
        error="rate limited",
    )
    status = storage.poll_queue_status()

    assert status["summary"]["errors"] == 1
    assert status["tasks"][0]["attempts"] == 1
    assert status["tasks"][0]["lastError"] == "rate limited"
