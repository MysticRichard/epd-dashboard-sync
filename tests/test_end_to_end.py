from datetime import datetime
from pathlib import Path

from epd_dashboard_sync.sync import sync_once
from tests.test_sync import SyncTransport, config_for


NOW = datetime.fromisoformat("2026-08-30T10:00:00+08:00")


def test_fixture_feed_to_two_slot_sync(tmp_path: Path) -> None:
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport()
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.codex.succeeded is True
    assert result.github.succeeded is True
    committed = {
        (payload[1], payload[2])
        for payload, _ in transport.writes
        if len(payload) >= 3 and payload[0] == 0x31 and payload[1] != 0
    }
    assert committed == {(3, 2), (2, 3)}
