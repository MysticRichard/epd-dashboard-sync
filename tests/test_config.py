from pathlib import Path

import pytest

from epd_dashboard_sync.config import ConfigError, load_config


def test_load_config_preserves_unconfigured_read_only_state(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "feed_path = '/Volumes/<NAS_MOUNT>/dashboard.json'\n"
        "[slots]\n"
        "configured = false\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.slots.configured is False


def test_load_config_rejects_duplicate_or_protected_slots(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "feed_path = '/Volumes/<NAS_MOUNT>/dashboard.json'\n"
        "[slots]\n"
        "codex = 13\n"
        "github = 13\n"
        "protected = [13]\n"
        "configured = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="不同|受保护"):
        load_config(path)


def test_empty_device_identifier_means_auto_select(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "feed_path = '/tmp/dashboard.json'\n"
        "[device]\n"
        "identifier = ''\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.device.identifier is None


def test_load_config_supports_local_codex_snapshot_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('feed_path = "feed.json"\ncodex_home = "~/custom-codex"\nlocal_snapshot_output = "state/codex.json"\nlocal_stale_after_seconds = 1200\n', encoding="utf-8")
    config = load_config(path)
    assert config.codex_home == Path("~/custom-codex").expanduser()
    assert config.local_snapshot_output == Path("state/codex.json")
    assert config.local_stale_after_seconds == 1200


def test_load_config_defaults_to_activating_written_slot(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("feed_path = 'feed.json'\n", encoding="utf-8")
    config = load_config(path)
    assert config.activate_after_sync is True


def test_clock_config_requires_identifier_when_enabled(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('feed_path = "feed.json"\n[clock]\nenabled = true\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="clock.identifier"):
        load_config(path)
