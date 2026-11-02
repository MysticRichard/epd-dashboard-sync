import json
from datetime import datetime
from pathlib import Path

from epd_dashboard_sync.cli import main
from epd_dashboard_sync.codex_local import snapshot_from_account_usage


def test_codex_snapshot_command_rejects_local_session_fallback(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("epd_dashboard_sync.cli.collect_account_codex", lambda *args, **kwargs: None)
    home = tmp_path / "codex"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "rollout.jsonl").write_text(
        '{"timestamp":"2026-08-31T11:00:00+08:00","type":"token_count","token_count":{"total_token_usage":100,"last_token_usage":100}}\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    output = tmp_path / "out.json"
    config_path.write_text(
        'feed_path = "feed.json"\ncodex_home = "%s"\nlocal_snapshot_output = "%s"\n' % (home.as_posix(), output.as_posix()), encoding="utf-8"
    )
    assert main(["codex-snapshot", "--config", str(config_path)]) == 2
    assert not output.exists()
    assert "unavailable" in capsys.readouterr().out.lower()


def test_codex_snapshot_retries_account_usage_before_local_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "codex"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "rollout.jsonl").write_text(
        '{"timestamp":"2026-08-31T11:00:00+08:00","type":"token_count","token_count":{"total_token_usage":100,"last_token_usage":100}}\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    output = tmp_path / "out.json"
    config_path.write_text(
        'feed_path = "feed.json"\ncodex_home = "%s"\nlocal_snapshot_output = "%s"\n' % (home.as_posix(), output.as_posix()), encoding="utf-8"
    )
    calls = []

    def account_usage(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return None
        return snapshot_from_account_usage(
            {"summary": {"lifetimeTokens": 123_456_789}, "dailyUsageBuckets": [{"startDate": "2026-09-02", "tokens": 23_456_789}]},
            now=kwargs["now"],
        )

    monkeypatch.setattr("epd_dashboard_sync.cli.collect_account_codex", account_usage)
    assert main(["codex-snapshot", "--config", str(config_path)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert payload["codex"]["tokenUsage"]["source"] == "codex-app-server-account-usage"
    assert payload["codex"]["tokenUsage"]["total"] == 123_456_789


def test_codex_snapshot_preserves_fresh_account_value_on_untrusted_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "codex"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "rollout.jsonl").write_text(
        '{"timestamp":"2026-09-02T12:00:00+08:00","type":"token_count","token_count":{"total_token_usage":-1,"last_token_usage":2}}\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    output = tmp_path / "out.json"
    config_path.write_text(
        'feed_path = "feed.json"\ncodex_home = "%s"\nlocal_snapshot_output = "%s"\n' % (home.as_posix(), output.as_posix()), encoding="utf-8"
    )
    previous = snapshot_from_account_usage(
        {"summary": {"lifetimeTokens": 123_456_789}, "dailyUsageBuckets": []},
        now=datetime.now().astimezone(),
    ).as_dict(generated_at=datetime.now().astimezone())
    output.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr("epd_dashboard_sync.cli.collect_account_codex", lambda *args, **kwargs: None)

    assert main(["codex-snapshot", "--config", str(config_path)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == previous
