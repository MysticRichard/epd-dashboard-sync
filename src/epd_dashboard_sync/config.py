from __future__ import annotations

import re
import uuid
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - compatibility for the local macOS Python 3.9
    import tomli as tomllib

from .errors import ConfigError


DEFAULT_SERVICE_UUID = "62750001-d828-918d-fb46-b6c11c675aec"


@dataclass(frozen=True)
class DeviceConfig:
    service_uuid: str = DEFAULT_SERVICE_UUID
    identifier: Optional[str] = None


@dataclass(frozen=True)
class ClockConfig:
    enabled: bool = False
    identifier: Optional[str] = None


@dataclass(frozen=True)
class SlotConfig:
    codex: int = 0
    github: int = 0
    protected: Tuple[int, ...] = ()
    configured: bool = False


@dataclass(frozen=True)
class AppConfig:
    feed_path: Path
    scan_timeout_seconds: float = 8.0
    sync_interval_seconds: int = 600
    stale_after_seconds: int = 86400
    device: DeviceConfig = DeviceConfig()
    clock: ClockConfig = ClockConfig()
    slots: SlotConfig = SlotConfig()
    codex_home: Path = Path.home() / ".codex"
    local_snapshot_output: Path = Path("state/codex-snapshot.json")
    local_stale_after_seconds: int = 900
    activate_after_sync: bool = True


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s 必须是整数" % name)
    return value


def _as_positive(value: Any, name: str, integer: bool = False) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError("%s 必须是正数" % name)
    if integer and int(value) != value:
        raise ConfigError("%s 必须是正整数" % name)
    return int(value) if integer else float(value)


def _as_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError("%s 必须是布尔值" % name)
    return value


def _read_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("service_uuid 必须是 UUID 字符串")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ConfigError("service_uuid 不是有效 UUID") from exc


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw: Dict[str, Any] = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError("配置文件不存在：%s" % path) from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("配置文件无法读取：%s" % exc) from exc

    feed_value = raw.get("feed_path")
    if not isinstance(feed_value, str) or not feed_value.strip():
        raise ConfigError("feed_path 必须是非空路径")

    device_raw = raw.get("device", {})
    if not isinstance(device_raw, dict):
        raise ConfigError("[device] 必须是表")
    service_uuid = _read_uuid(device_raw.get("service_uuid", DEFAULT_SERVICE_UUID))
    identifier = device_raw.get("identifier")
    if identifier == "":
        identifier = None
    if identifier is not None and (not isinstance(identifier, str) or not identifier.strip()):
        raise ConfigError("device.identifier 必须是非空字符串或省略")

    clock_raw = raw.get("clock", {})
    if not isinstance(clock_raw, dict):
        raise ConfigError("[clock] 必须是表")
    clock_enabled = clock_raw.get("enabled", False)
    if not isinstance(clock_enabled, bool):
        raise ConfigError("clock.enabled 必须是布尔值")
    clock_identifier = clock_raw.get("identifier")
    if clock_identifier == "":
        clock_identifier = None
    if clock_identifier is not None and (not isinstance(clock_identifier, str) or not clock_identifier.strip()):
        raise ConfigError("clock.identifier 必须是非空字符串或省略")
    if clock_enabled and clock_identifier is None:
        raise ConfigError("启用时钟屏时必须配置 clock.identifier")

    slots_raw = raw.get("slots", {})
    if not isinstance(slots_raw, dict):
        raise ConfigError("[slots] 必须是表")
    configured = slots_raw.get("configured", False)
    if not isinstance(configured, bool):
        raise ConfigError("slots.configured 必须是布尔值")
    codex = _as_int(slots_raw.get("codex", 0), "slots.codex")
    github = _as_int(slots_raw.get("github", 0), "slots.github")
    protected_raw = slots_raw.get("protected", [])
    if not isinstance(protected_raw, list):
        raise ConfigError("slots.protected 必须是整数数组")
    protected = tuple(_as_int(value, "slots.protected") for value in protected_raw)
    all_slots = (codex, github)
    if any(slot < 0 for slot in protected + all_slots):
        raise ConfigError("槽位索引不能为负数")
    if configured:
        if codex == github:
            raise ConfigError("Codex 和 GitHub 槽位必须不同")
        if codex in protected or github in protected:
            raise ConfigError("配置槽位不能使用受保护槽位")

    return AppConfig(
        feed_path=Path(feed_value).expanduser(),
        scan_timeout_seconds=_as_positive(raw.get("scan_timeout_seconds", 8), "scan_timeout_seconds"),
        sync_interval_seconds=_as_positive(raw.get("sync_interval_seconds", 600), "sync_interval_seconds", integer=True),
        stale_after_seconds=_as_positive(raw.get("stale_after_seconds", 86400), "stale_after_seconds", integer=True),
        device=DeviceConfig(service_uuid=service_uuid, identifier=identifier),
        clock=ClockConfig(enabled=clock_enabled, identifier=clock_identifier),
        slots=SlotConfig(codex=codex, github=github, protected=protected, configured=configured),
        codex_home=Path(raw.get("codex_home", os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))).expanduser(),
        local_snapshot_output=Path(raw.get("local_snapshot_output", "state/codex-snapshot.json")).expanduser(),
        local_stale_after_seconds=_as_positive(raw.get("local_stale_after_seconds", 900), "local_stale_after_seconds", integer=True),
        activate_after_sync=_as_bool(raw.get("activate_after_sync", True), "activate_after_sync"),
    )
