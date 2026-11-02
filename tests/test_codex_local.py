from datetime import datetime, timedelta, timezone
from pathlib import Path

import json

import epd_dashboard_sync.codex_local as codex_local
from epd_dashboard_sync.codex_local import (
    collect_account_codex,
    collect_local_codex,
    snapshot_from_account_usage,
    windows_from_rate_limits,
)


NOW = datetime.fromisoformat("2026-08-31T12:00:00+08:00")


def _write_rollout(root: Path, name: str, lines: list[str], *, mtime: datetime = NOW) -> None:
    path = root / "sessions" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stamp = mtime.timestamp()
    path.touch()
    import os

    os.utime(path, (stamp, stamp))


def test_collects_totals_last_seven_days_and_duration_windows(tmp_path: Path) -> None:
    _write_rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            '{"timestamp":"2026-08-25T10:00:00+08:00","type":"token_count","token_count":{"total_token_usage":1000,"last_token_usage":1000}}',
            '{"timestamp":"2026-08-30T10:00:00+08:00","type":"token_count","token_count":{"total_token_usage":1600,"last_token_usage":600}}',
            '{"timestamp":"2026-08-31T11:00:00+08:00","type":"token_count","token_count":{"totalTokenUsage":2100,"lastTokenUsage":500}}',
            '{"timestamp":"2026-08-31T11:30:00+08:00","type":"event_msg","rate_limits":{"primary":{"used_percent":32,"window_duration_mins":300,"resets_at":"2026-08-31T14:00:00+08:00"},"secondary":{"usedPercent":58,"windowDurationMins":10080,"resetsAt":"2026-09-03T08:00:00+08:00"}}}',
        ],
    )
    snapshot = collect_local_codex(tmp_path, now=NOW, lookback=timedelta(days=7), stale_after=timedelta(hours=2))

    assert snapshot.available is True
    assert snapshot.total == 2100
    assert snapshot.last_7_days == 2100
    assert snapshot.windows[300].used_percent == 32
    assert snapshot.windows[10080].resets_at.isoformat().startswith("2026-09-03")
    assert snapshot.warnings == ()


def test_deduplicates_events_and_rejects_malformed_counters(tmp_path: Path) -> None:
    line = '{"id":"same","timestamp":"2026-08-31T11:00:00+08:00","type":"token_count","payload":{"token_count":{"total_token_usage":900,"last_token_usage":200}}}'
    _write_rollout(tmp_path, "rollout.jsonl", [line, line, '{"type":"token_count","token_count":{"total_token_usage":-1,"last_token_usage":2}}'])

    snapshot = collect_local_codex(tmp_path, now=NOW, lookback=timedelta(days=7), stale_after=timedelta(minutes=15))

    assert snapshot.total == 900
    assert snapshot.last_7_days == 200
    assert "invalid_token_count" in snapshot.warnings


def test_collects_current_codex_info_usage_shape(tmp_path: Path) -> None:
    _write_rollout(
        tmp_path,
        "rollout-current.jsonl",
        [
            '{"timestamp":"2026-08-31T10:00:00+08:00","type":"event_msg","payload":{"info":{"total_token_usage":{"total_tokens":1000},"last_token_usage":{"total_tokens":1000}}}}',
            '{"timestamp":"2026-08-31T11:00:00+08:00","type":"event_msg","payload":{"info":{"total_token_usage":{"total_tokens":1234},"last_token_usage":{"total_tokens":234}},"rate_limits":{"primary":{"used_percent":38,"window_minutes":300,"resets_at":1788271920},"secondary":{"used_percent":67,"window_minutes":10080,"resets_at":1788747977}}}}',
        ],
    )

    snapshot = collect_local_codex(tmp_path, now=NOW, lookback=timedelta(days=7), stale_after=timedelta(hours=2))

    assert snapshot.available is True
    assert snapshot.total == 1234
    assert snapshot.last_7_days == 1234
    assert snapshot.windows[300].used_percent == 38
    assert snapshot.windows[10080].resets_at is not None
    assert snapshot.warnings == ()


def test_missing_or_stale_logs_are_unavailable_with_warning(tmp_path: Path) -> None:
    snapshot = collect_local_codex(tmp_path, now=NOW, lookback=timedelta(days=7), stale_after=timedelta(minutes=15))
    assert snapshot.available is False
    assert snapshot.total is None
    assert "no_rollouts" in snapshot.warnings

    _write_rollout(tmp_path, "old.jsonl", ['{"timestamp":"2026-08-30T10:00:00+08:00","type":"token_count","token_count":{"total_token_usage":4,"last_token_usage":4}}'], mtime=NOW - timedelta(hours=2))
    stale = collect_local_codex(tmp_path, now=NOW, lookback=timedelta(days=7), stale_after=timedelta(minutes=15))
    assert stale.stale is True
    assert "stale_local_data" in stale.warnings


def test_account_usage_uses_lifetime_total_and_rolling_seven_days() -> None:
    response = {
        "summary": {"lifetimeTokens": 123_456_789},
        "weeklyUsageBuckets": [{"startDate": "2026-08-31", "tokens": 34_567_890}],
        "dailyUsageBuckets": [
            {"startDate": "2026-08-30", "tokens": 45_678_901},
            {"startDate": "2026-08-31", "tokens": 12_345_678},
            {"startDate": "2026-09-01", "tokens": 23_456_789},
            {"startDate": "2026-09-02", "tokens": 100},
        ],
    }

    snapshot = snapshot_from_account_usage(
        response,
        now=datetime.fromisoformat("2026-09-02T10:00:00+08:00"),
    )

    assert snapshot.total == 123_456_789
    assert snapshot.last_7_days == 81_481_468
    assert snapshot.peak_daily == 45_678_901
    assert snapshot.source == "codex-app-server-account-usage"

    daily_snapshot = snapshot_from_account_usage(
        {"dailyUsageBuckets": response["dailyUsageBuckets"]},
        now=datetime.fromisoformat("2026-09-02T10:00:00+08:00"),
    )
    assert daily_snapshot.last_7_days == 81_481_468


def test_account_usage_excludes_buckets_before_provider_week() -> None:
    snapshot = snapshot_from_account_usage(
        {
            "dailyUsageBuckets": [
                {"startDate": "2026-09-01", "tokens": 2},
                {"startDate": "2026-09-02", "tokens": 3},
                {"startDate": "2026-09-08", "tokens": 5},
            ]
        },
        now=datetime.fromisoformat("2026-09-08T10:00:00+08:00"),
    )

    assert snapshot.last_7_days == 5


def test_rate_limit_response_maps_primary_and_secondary_windows() -> None:
    windows = windows_from_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": 1793340216},
                "secondary": {"usedPercent": 62, "windowDurationMins": 10080, "resetsAt": 1793774285},
            }
        },
        now=NOW,
    )

    assert windows[300].used_percent == 5
    assert windows[300].resets_at.isoformat() == "2026-10-30T06:03:36+00:00"
    assert windows[10080].used_percent == 62


def test_account_usage_matches_provider_sunday_week_to_date() -> None:
    snapshot = snapshot_from_account_usage(
        {
            "dailyUsageBuckets": [
                {"startDate": "2026-09-08", "tokens": 11_111_111},
                {"startDate": "2026-09-09", "tokens": 22_222_222},
                {"startDate": "2026-09-10", "tokens": 33_333_333},
                {"startDate": "2026-09-11", "tokens": 44_444_444},
                {"startDate": "2026-09-12", "tokens": 55_555_555},
                {"startDate": "2026-09-13", "tokens": 66_666_666},
            ]
        },
        now=datetime.fromisoformat("2026-09-14T09:00:00+08:00"),
    )

    assert snapshot.last_7_days == 66_666_666


def test_account_collector_timeout_uses_elapsed_time_not_observation_timestamp(tmp_path: Path, monkeypatch) -> None:
    class _Pipe:
        def write(self, value: str) -> None:
            del value

        def flush(self) -> None:
            return None

    class _Process:
        stdin = _Pipe()
        stdout = iter([json.dumps({"id": 2, "result": {"summary": {"lifetimeTokens": 3}}})])

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            del timeout
            return 0

    popen_kwargs = {}

    def fake_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return _Process()

    monkeypatch.setattr(codex_local.subprocess, "Popen", fake_popen)
    snapshot = collect_account_codex(
        tmp_path,
        now=datetime.fromisoformat("2020-01-01T00:00:00+08:00"),
        executable="codex",
    )

    assert snapshot is not None
    assert snapshot.total == 3
    assert popen_kwargs["creationflags"] == codex_local.subprocess.CREATE_NO_WINDOW
