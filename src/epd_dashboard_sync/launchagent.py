"""macOS per-user LaunchAgent generation and guarded installation."""

from __future__ import annotations

import plistlib
import shlex
from pathlib import Path
from typing import Optional

from .state import SyncState, default_state_path


class CliError(RuntimeError):
    pass


LABEL = "dev.epd-dashboard-sync.agent"


def _shell_quote(value: Path) -> str:
    return shlex.quote(str(value))


def render_launch_agent(config_path: Path, executable: Path) -> str:
    log_dir = Path.home() / "Library" / "Logs" / "epd-dashboard-sync"
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/sh", "-lc",
            "%s codex-snapshot --config %s && %s sync-once --config %s --only codex --combined" % (
                _shell_quote(executable), _shell_quote(Path(config_path)),
                _shell_quote(executable), _shell_quote(Path(config_path)),
            ),
        ],
        "RunAtLoad": True,
        "StartInterval": 600,
        "StandardOutPath": str(log_dir / "stdout.log"),
        "StandardErrorPath": str(log_dir / "stderr.log"),
        "ProcessType": "Background",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def _has_success_marker(launch_agents_dir: Optional[Path]) -> bool:
    # A caller-provided directory is an isolated installation target (and is
    # used by tests); do not let an unrelated global state marker authorize it.
    candidates = [Path(launch_agents_dir) / "state.json"] if launch_agents_dir is not None else [default_state_path()]
    return any(SyncState.load(path).last_result == "success" for path in candidates)


def install_launch_agent(config_path: Path, executable: Path, launch_agents_dir: Optional[Path] = None) -> Path:
    if not _has_success_marker(launch_agents_dir):
        raise CliError("安装自动同步前必须先完成一次成功的 sync-once")
    target_dir = Path(launch_agents_dir) if launch_agents_dir is not None else Path.home() / "Library" / "LaunchAgents"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (LABEL + ".plist")
    target.write_text(render_launch_agent(config_path, executable), encoding="utf-8")
    return target


def uninstall_launch_agent(launch_agents_dir: Optional[Path] = None) -> Path:
    target_dir = Path(launch_agents_dir) if launch_agents_dir is not None else Path.home() / "Library" / "LaunchAgents"
    target = target_dir / (LABEL + ".plist")
    if target.exists():
        target.unlink()
    return target
