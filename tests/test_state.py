from datetime import datetime, timezone
from pathlib import Path

from epd_dashboard_sync.state import SyncState


def test_state_round_trip_contains_only_sync_metadata(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = SyncState(
        device_identifier="device-1",
        hashes={"codex": "a" * 64, "github": "b" * 64},
        last_success_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        consecutive_failures=2,
        last_result="partial",
    )
    state.save(path)
    loaded = SyncState.load(path)
    assert loaded == state
    assert "token" not in path.read_text(encoding="utf-8")


def test_missing_state_is_empty(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "missing.json")
    assert state.device_identifier is None
    assert state.hashes == {}
    assert state.consecutive_failures == 0
