from pathlib import Path

import pytest

from epd_dashboard_sync.launchagent import CliError, install_launch_agent, render_launch_agent


def test_install_requires_success_marker(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="成功"):
        install_launch_agent(tmp_path / "config.toml", executable=Path("/tmp/epd-dashboard"), launch_agents_dir=tmp_path)


def test_render_launch_agent_contains_opportunistic_schedule(tmp_path: Path) -> None:
    plist = render_launch_agent(tmp_path / "config.toml", Path("/tmp/epd-dashboard"))
    assert "dev.epd-dashboard-sync.agent" in plist
    assert "StartInterval" in plist
    assert "600" in plist
    assert "--combined" in plist
    assert "codex-snapshot" in plist
