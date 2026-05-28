from __future__ import annotations

from twitter_cli.models import UserProfile
from twitter_monitor.storage import MonitorStorage


def test_following_insights_separates_project_and_hunter_candidates(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    alice = storage.add_target("alice", group_name="alpha-a")
    bob = storage.add_target("bob", group_name="alpha-b")
    project = UserProfile(
        id="project-1",
        name="Bound Network",
        screen_name="bound_network",
        bio="Official DeFi protocol and exchange network.",
        followers_count=9000,
        following_count=120,
        url="https://bound.network",
    )
    hunter = UserProfile(
        id="hunter-1",
        name="Nina Alpha",
        screen_name="nina_alpha",
        bio="Onchain alpha researcher, airdrop hunter and crypto analyst.",
        followers_count=8000,
        following_count=900,
    )
    storage.upsert_followed_users([project, hunter])
    storage.add_seen_following(int(alice["id"]), ["project-1", "hunter-1"])
    storage.add_seen_following(int(bob["id"]), ["project-1", "hunter-1"])

    insights = storage.following_insights(min_common=2)

    assert [item["handle"] for item in insights["projectCandidates"]] == ["bound_network"]
    assert [item["handle"] for item in insights["hunterCandidates"]] == ["nina_alpha"]
    assert insights["hunterCandidates"][0]["hunterScore"] >= 45
    assert insights["summary"]["projectAccounts"] == 1
    assert insights["summary"]["hunterCandidates"] == 1


def test_hunter_candidates_exclude_existing_monitored_targets(tmp_path) -> None:
    storage = MonitorStorage(str(tmp_path / "monitor.db"))
    storage.init()
    alice = storage.add_target("alice", group_name="alpha-a")
    bob = storage.add_target("bob", group_name="alpha-b")
    storage.add_target("nina_alpha", group_name="候选猎手")
    hunter = UserProfile(
        id="hunter-1",
        name="Nina Alpha",
        screen_name="nina_alpha",
        bio="Onchain alpha researcher and airdrop hunter.",
        followers_count=8000,
        following_count=900,
    )
    storage.upsert_followed_users([hunter])
    storage.add_seen_following(int(alice["id"]), ["hunter-1"])
    storage.add_seen_following(int(bob["id"]), ["hunter-1"])

    insights = storage.following_insights(min_common=2)

    assert insights["hunterCandidates"] == []
