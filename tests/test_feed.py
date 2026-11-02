from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from epd_dashboard_sync.feed import FeedError, load_feed


NOW = datetime.fromisoformat("2026-08-30T10:00:00+08:00")
FIXTURES = Path(__file__).parent / "fixtures"


def test_load_feed_normalizes_remaining_quota_and_private_repo() -> None:
    feed = load_feed(FIXTURES / "dashboard-valid.json", now=NOW, stale_after=timedelta(hours=24))

    assert feed.quota.five_hour.remaining_percent == 68.0
    assert feed.repositories[0].private is True
    assert feed.repositories[0].workflow.conclusion == "success"


def test_load_feed_rejects_stale_snapshot() -> None:
    with pytest.raises(FeedError, match="过期"):
        load_feed(FIXTURES / "dashboard-stale.json", now=NOW, stale_after=timedelta(hours=24))


def test_load_feed_parses_token_usage_contributions_and_commit_counts() -> None:
    feed = load_feed(FIXTURES / "dashboard-with-usage-and-github.json", now=NOW, stale_after=timedelta(hours=24))

    assert feed.token_usage.available is True
    assert feed.token_usage.five_hour.used == 1200
    assert feed.token_usage.weekly.limit == 10000
    assert feed.contributions[-1].count == 7
    assert feed.contribution_total == 7
    assert feed.repositories[0].commit_count == 34


def test_load_feed_preserves_provider_contribution_total(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "github": {
            "contributions": {
                "lastYear": [{"date": "2026-08-29", "count": 7}],
                "totalContributions": 120,
            },
        },
        "repositories": [],
        "dataWarnings": [],
    }
    path = tmp_path / "github-total.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    feed = load_feed(path, now=NOW, stale_after=timedelta(hours=24))

    assert feed.contribution_total == 120


def test_load_feed_marks_missing_token_usage_as_unavailable() -> None:
    feed = load_feed(FIXTURES / "dashboard-valid.json", now=NOW, stale_after=timedelta(hours=24))

    assert feed.token_usage.available is False
    assert feed.token_usage.five_hour is None
    assert any("token" in warning.message for warning in feed.warnings)


def test_load_feed_keeps_codex_visible_when_github_has_no_repositories(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {"fiveHour": {"remainingPercent": 68}, "weekly": {"remainingPercent": 42}},
        "codex": {"tokenUsage": {"available": False, "fiveHour": None, "weekly": None}},
        "github": {"contributions": {"lastYear": [], "lastWeek": []}},
        "repositories": [],
        "dataWarnings": ["github_not_configured"],
    }
    path = tmp_path / "quota-only.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    feed = load_feed(path, now=NOW, stale_after=timedelta(hours=24))
    assert feed.repositories == []
    assert any("github_not_configured" in warning.message for warning in feed.warnings)


def test_load_feed_parses_local_codex_aggregate_and_duration_windows() -> None:
    feed = load_feed(FIXTURES / "dashboard-local-codex.json", now=datetime.fromisoformat("2026-08-31T12:00:00+08:00"), stale_after=timedelta(hours=24))

    assert feed.token_usage.total == 123_456_789
    assert feed.token_usage.last_7_days == 23_456_789
    assert feed.token_usage.source == "local-session-log"
    assert feed.quota.five_hour.remaining_percent == 68


def test_load_feed_overlays_fresh_local_quota_snapshot(tmp_path: Path) -> None:
    local = {
        "schemaVersion": 1,
        "generatedAt": "2026-08-30T09:55:00+08:00",
        "codex": {
            "tokenUsage": {"available": True, "total": 200, "last7Days": 180},
            "windows": [
                {"durationMinutes": 300, "usedPercent": 28, "resetsAt": "2026-08-30T14:00:00+08:00"},
                {"durationMinutes": 10080, "usedPercent": 73, "resetsAt": "2026-09-03T08:00:00+08:00"},
            ],
        },
    }
    path = tmp_path / "codex-snapshot.json"
    path.write_text(json.dumps(local), encoding="utf-8")

    feed = load_feed(
        FIXTURES / "dashboard-valid.json",
        now=NOW,
        stale_after=timedelta(hours=24),
        local_snapshot_path=path,
        local_stale_after=timedelta(minutes=15),
    )

    assert feed.quota.five_hour.remaining_percent == 72
    assert feed.quota.weekly.remaining_percent == 27
    assert feed.token_usage.total == 200
    assert feed.token_usage.last_7_days == 180


def test_load_feed_accepts_github_nested_repository_payload(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "github": {
            "contributions": {"lastYear": [{"date": "2026-08-29", "count": 3}]},
            "repositories": [{
                "fullName": "example-org/example",
                "private": True,
                "defaultBranch": "main",
                "commitCount": 3,
            }],
        },
    }
    path = tmp_path / "nested-github.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    feed = load_feed(path, now=NOW, stale_after=timedelta(hours=24))
    assert [repo.full_name for repo in feed.repositories] == ["example-org/example"]
    assert feed.contributions[0].count == 3


def test_load_feed_preserves_github_contribution_visibility_status(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "github": {
            "contributions": {"lastYear": [], "lastWeek": []},
            "contributionStatus": "private_hidden",
            "contributionWarnings": ["github_private_contributions_hidden"],
        },
        "repositories": [],
        "dataWarnings": [],
    }
    path = tmp_path / "github-private-hidden.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    feed = load_feed(path, now=NOW, stale_after=timedelta(hours=24))
    assert feed.contribution_status == "private_hidden"
    assert any("github_private_contributions_hidden" in warning.message for warning in feed.warnings)


def test_load_feed_preserves_unverified_private_contribution_status(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "github": {
            "contributions": {"lastYear": [], "lastWeek": []},
            "contributionStatus": "private_unverified",
            "contributionWarnings": ["github_private_visibility_unverified"],
        },
        "repositories": [],
        "dataWarnings": [],
    }
    path = tmp_path / "github-private-unverified.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    feed = load_feed(path, now=NOW, stale_after=timedelta(hours=24))

    assert feed.contribution_status == "private_unverified"
    assert any("github_private_visibility_unverified" in warning.message for warning in feed.warnings)
