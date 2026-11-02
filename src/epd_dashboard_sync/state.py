"""Small, non-sensitive persistent state for idempotent synchronisation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


def default_state_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "epd-dashboard-sync" / "state.json"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "epd-dashboard-sync" / "state.json"
    return Path.home() / ".local" / "share" / "epd-dashboard-sync" / "state.json"


@dataclass
class SyncState:
    device_identifier: Optional[str] = None
    hashes: Dict[str, str] = field(default_factory=dict)
    last_success_at: Optional[datetime] = None
    consecutive_failures: int = 0
    last_result: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "SyncState":
        path = Path(path) if path is not None else default_state_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        last_success = raw.get("lastSuccessAt")
        parsed_success = None
        if isinstance(last_success, str):
            try:
                parsed_success = datetime.fromisoformat(last_success)
            except ValueError:
                parsed_success = None
        hashes = raw.get("hashes", {})
        if not isinstance(hashes, dict):
            hashes = {}
        safe_hashes = {key: value for key, value in hashes.items() if key in ("codex", "github") and isinstance(value, str)}
        failures = raw.get("consecutiveFailures", 0)
        if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
            failures = 0
        return cls(
            device_identifier=raw.get("deviceIdentifier") if isinstance(raw.get("deviceIdentifier"), str) else None,
            hashes=safe_hashes,
            last_success_at=parsed_success,
            consecutive_failures=failures,
            last_result=raw.get("lastResult") if isinstance(raw.get("lastResult"), str) else None,
        )

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path is not None else default_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "deviceIdentifier": self.device_identifier,
            "hashes": {key: self.hashes[key] for key in ("codex", "github") if key in self.hashes},
            "lastSuccessAt": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutiveFailures": self.consecutive_failures,
            "lastResult": self.last_result,
        }
        fd, temp_name = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, str(path))
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
