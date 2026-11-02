from pathlib import Path

from epd_dashboard_sync.cli import main


def test_render_command_writes_two_pngs(tmp_path: Path) -> None:
    fixture_feed = Path(__file__).parent / "fixtures" / "dashboard-valid.json"
    assert main(["render", "--feed", str(fixture_feed), "--output", str(tmp_path), "--profile", "4.2_400_300"]) == 0
    assert (tmp_path / "codex.png").exists()
    assert (tmp_path / "github.png").exists()


def test_discover_command_prints_slot_inventory(monkeypatch, capsys, tmp_path: Path) -> None:
    class FakeTransport:
        def __init__(self, *args):
            self.callback = None

        def scan(self, timeout=8):
            return ["device-1"]

        def connect(self, identifier):
            pass

        def read_version(self):
            return b"v1.10"

        def start_notifications(self, callback):
            self.callback = callback

        def write(self, payload, response=True):
            payload = bytes(payload)
            if payload == bytes([0x35]):
                self.callback(b"slots=15 0 15 12 2 1048576 fp0 fp1 fp2")
            elif payload == bytes([0x01]):
                self.callback(bytes([0, 1, 2, 3, 4, 5, 6, 0x03, 0, 0, 7, 8, 9, 10, 1, 0, 0, 0, 0]))
                self.callback(b"mtu=247")

        def disconnect(self):
            pass

    config = tmp_path / "config.toml"
    config.write_text("feed_path = '/tmp/dashboard.json'\n", encoding="utf-8")
    monkeypatch.setattr("epd_dashboard_sync.cli.BleakTransport", FakeTransport)
    assert main(["discover", "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "slots=15" in output


def test_sync_command_prints_result_and_failure(monkeypatch, capsys, tmp_path: Path) -> None:
    from types import SimpleNamespace

    config = tmp_path / "config.toml"
    config.write_text("feed_path = '/tmp/dashboard.json'\n", encoding="utf-8")
    failure = SimpleNamespace(
        codex=SimpleNamespace(updated=False, skipped=False, failed=True, error="未收到 ready=1"),
        github=SimpleNamespace(updated=False, skipped=True, failed=False, error=None),
        error_class="transfer",
    )
    monkeypatch.setattr("epd_dashboard_sync.cli.sync_once", lambda *args, **kwargs: failure)
    assert main(["sync-once", "--config", str(config), "--only", "codex"]) == 1
    output = capsys.readouterr().out
    assert "codex" in output
    assert "未收到 ready=1" in output
