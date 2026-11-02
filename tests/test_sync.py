from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from epd_dashboard_sync.config import AppConfig, ClockConfig, DeviceConfig, SlotConfig
from epd_dashboard_sync.feed import load_feed
from epd_dashboard_sync.profiles import FOUR_TWO_PROFILE
from epd_dashboard_sync.render import render_dashboards
from epd_dashboard_sync.state import SyncState
from epd_dashboard_sync.sync import sync_once


NOW = datetime.fromisoformat("2026-08-30T10:00:00+08:00")


class SyncTransport:
    def __init__(self, fail_after_image_chunk=False, driver=0x05, ready_on_chunk=True):
        self.fail_after_image_chunk = fail_after_image_chunk
        self.driver = driver
        self.ready_on_chunk = ready_on_chunk
        self.writes = []
        self.scan_calls = 0
        self.callback = None

    def scan(self, timeout=8):
        self.scan_calls += 1
        return ["device-1"]

    def connect(self, identifier):
        assert identifier == "device-1"

    def read_version(self):
        return b"v1.10"

    def start_notifications(self, callback):
        self.callback = callback

    def write(self, payload, response=True):
        payload = bytes(payload)
        self.writes.append((payload, response))
        if payload == bytes([0x35]):
            self.callback(b"slots=15 0 15 12 2 1048576 fp0 fp1 fp2")
        elif payload == bytes([0x01]):
            self.callback(bytes([0, 1, 2, 3, 4, 5, 6, self.driver, 0, 0, 7, 8, 9, 10, 1, 0, 0, 0, 0]))
            self.callback(b"mtu=247")
        elif payload and payload[0] == 0x30:
            if self.fail_after_image_chunk:
                raise RuntimeError("simulated transfer failure")
            if self.ready_on_chunk:
                self.callback(b"ready=1")
        elif len(payload) >= 3 and payload[:2] in (bytes([0x31, 2]), bytes([0x31, 3])):
            if not self.ready_on_chunk:
                self.callback(b"ready=1")

    def disconnect(self):
        pass


def config_for(feed_path: Path, configured: bool = True) -> AppConfig:
    return AppConfig(
        feed_path=feed_path,
        device=DeviceConfig(identifier="device-1"),
        slots=SlotConfig(codex=2, github=3, configured=configured),
    )


def test_unconfigured_slots_never_write(tmp_path: Path):
    transport = SyncTransport()
    config = config_for(Path(__file__).parent / "fixtures" / "dashboard-valid.json", configured=False)
    result = sync_once(config, lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.skipped_reason == "slots_not_configured"
    assert transport.writes == []
    assert transport.scan_calls == 0


def test_failed_upload_does_not_send_commit(tmp_path: Path):
    transport = SyncTransport(fail_after_image_chunk=True)
    config = config_for(Path(__file__).parent / "fixtures" / "dashboard-valid.json")
    result = sync_once(config, lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.github.failed is True
    assert not any(payload[:2] == bytes([0x31, 2]) for payload, _ in transport.writes)


def test_same_hash_skips_ble_connection(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    feed = load_feed(feed_path, NOW, __import__("datetime").timedelta(hours=24))
    rendered = render_dashboards(feed, FOUR_TWO_PROFILE)
    state = SyncState(hashes={"codex": rendered["codex"].sha256, "github": rendered["github"].sha256})
    state.save(tmp_path / "state.json")
    transport = SyncTransport()
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.skipped_reason == "unchanged"
    assert transport.scan_calls == 0


def test_unchanged_dashboard_still_syncs_enabled_clock(monkeypatch, tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    feed = load_feed(feed_path, NOW, __import__("datetime").timedelta(hours=24))
    rendered = render_dashboards(feed, FOUR_TWO_PROFILE)
    state = SyncState(hashes={"codex": rendered["codex"].sha256, "github": rendered["github"].sha256})
    state.save(tmp_path / "state.json")
    calls = []
    monkeypatch.setattr("epd_dashboard_sync.sync.sync_clock", lambda identifier, now: calls.append((identifier, now)))
    config = config_for(feed_path)
    config = AppConfig(
        feed_path=config.feed_path,
        device=config.device,
        clock=ClockConfig(enabled=True, identifier="clock-1"),
        slots=config.slots,
    )
    transport = SyncTransport()
    result = sync_once(config, lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.skipped_reason == "unchanged"
    assert result.clock_synced is True
    assert calls == [("clock-1", NOW)]
    assert transport.scan_calls == 0


def test_three_color_sync_sends_black_then_red_planes(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport(driver=0x03)
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json")
    assert result.codex.succeeded is True
    image_payloads = [payload for payload, _ in transport.writes if payload and payload[0] == 0x30]
    assert image_payloads
    configs = [payload[1] for payload in image_payloads]
    assert configs[0] & 0x02
    assert 0x01 in configs


def test_sync_can_limit_update_to_one_authorized_slot(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport(driver=0x03)
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json", only=("codex",))
    assert result.codex.succeeded is True
    assert result.github.skipped is True
    github_commits = [payload for payload, _ in transport.writes if len(payload) >= 3 and payload[:2] == bytes([0x31, 2]) and payload[2] == 3]
    assert github_commits == []


def test_combined_sync_writes_the_single_codex_slot(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport(driver=0x03)
    result = sync_once(
        config_for(feed_path),
        lambda: transport,
        NOW,
        state_path=tmp_path / "state.json",
        only=("codex",),
        combined=True,
    )
    assert result.codex.succeeded is True
    assert result.github.skipped is True


def test_combined_sync_activates_codex_slot_after_commit_when_enabled(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport(driver=0x03, ready_on_chunk=False)
    config = config_for(feed_path)
    config = AppConfig(
        feed_path=config.feed_path,
        device=config.device,
        slots=config.slots,
        activate_after_sync=True,
    )
    result = sync_once(
        config,
        lambda: transport,
        NOW,
        state_path=tmp_path / "state.json",
        only=("codex",),
        combined=True,
    )
    assert result.codex.succeeded is True
    assert any(payload[:2] == bytes([0x31, 3]) for payload, _ in transport.writes)


def test_sync_waits_for_ready_after_slot_commit(tmp_path: Path):
    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    transport = SyncTransport(driver=0x03, ready_on_chunk=False)
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json", only=("codex",))
    assert result.codex.succeeded is True


def test_scan_failure_keeps_unselected_slot_skipped(tmp_path: Path):
    class MissingTransport(SyncTransport):
        def scan(self, timeout=8):
            self.scan_calls += 1
            return []

    feed_path = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    result = sync_once(config_for(feed_path), lambda: MissingTransport(), NOW, state_path=tmp_path / "state.json", only=("codex",))
    assert result.codex.failed is True
    assert result.github.skipped is True
    assert result.github.failed is False


def test_unavailable_combined_feed_never_overwrites_device(tmp_path: Path):
    feed_path = tmp_path / "unavailable.json"
    feed_path.write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "codex": {"tokenUsage": {"available": False}},
        "github": {
            "contributions": {"lastYear": [], "lastWeek": []},
            "contributionStatus": "unavailable",
        },
        "repositories": [],
        "dataWarnings": ["codex_token_usage_unavailable", "github_auto_select_unavailable"],
    }), encoding="utf-8")
    transport = SyncTransport()
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json", only=("codex",), combined=True)
    assert result.codex.failed is True
    assert "不可用" in (result.codex.error or "")
    assert transport.scan_calls == 0


def test_unavailable_codex_token_never_overwrites_combined_device(tmp_path: Path):
    feed_path = tmp_path / "unavailable-codex.json"
    feed_path.write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "codex": {"tokenUsage": {"available": False}},
        "github": {
            "contributions": {"lastYear": [], "lastWeek": []},
            "contributionStatus": "ok",
        },
        "repositories": [{
            "fullName": "owner/repo",
            "private": True,
            "defaultBranch": "main",
        }],
        "dataWarnings": ["codex_token_usage_unavailable"],
    }), encoding="utf-8")
    transport = SyncTransport()
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json", only=("codex",), combined=True)
    assert result.codex.failed is True
    assert "Token" in (result.codex.error or "")
    assert transport.scan_calls == 0


def test_stale_account_usage_never_overwrites_device(tmp_path: Path):
    feed_path = tmp_path / "stale-account.json"
    feed_path.write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "quota": {},
        "codex": {"tokenUsage": {
            "available": True,
            "total": 678_901_234,
            "last7Days": 78_901_234,
            "observedAt": (NOW - timedelta(hours=1)).isoformat(),
            "source": "codex-app-server-account-usage",
        }},
        "github": {"contributions": {"lastYear": [], "lastWeek": []}},
        "repositories": [],
    }), encoding="utf-8")
    transport = SyncTransport()
    result = sync_once(config_for(feed_path), lambda: transport, NOW, state_path=tmp_path / "state.json", only=("codex",), combined=True)
    assert result.codex.failed is True
    assert "过期" in (result.codex.error or "")
    assert transport.scan_calls == 0
