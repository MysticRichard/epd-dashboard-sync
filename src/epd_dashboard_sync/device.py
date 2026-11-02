"""Read-only device discovery and slot inventory parsing."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

from .config import DeviceConfig
from .errors import ConfigError
from .profiles import FOUR_TWO_PROFILE, FOUR_TWO_THREE_COLOR_PROFILE, ScreenProfile, THREE_NINETY_EIGHT_PROFILE
from .protocol import CMD_GET_SLOTS, CMD_INIT, encode_command

class DeviceError(RuntimeError):
    """Raised when the device reports incomplete or unsupported capabilities."""


@dataclass(frozen=True)
class DeviceProfile:
    screen_profile: ScreenProfile
    driver_id: str
    firmware_label: str
    width: int
    height: int
    color_mode: str
    protocol_version: str
    mtu: int


@dataclass(frozen=True)
class SlotInventory:
    count: int
    used_slots: Tuple[int, ...]
    selected_slot: Optional[int]
    flash_size: int
    fingerprints: Tuple[Optional[str], ...]
    page_start: int
    page_count: int
    protocol_version: str


def _decode_event(value: Any) -> Any:
    if isinstance(value, str):
        return value
    try:
        return bytes(value)
    except (TypeError, ValueError) as exc:
        raise DeviceError("设备通知不是有效字节数据") from exc


def _parse_mtu(events: Sequence[Any]) -> Optional[int]:
    for event in events:
        if isinstance(event, bytes):
            try:
                event = event.decode("ascii")
            except UnicodeDecodeError:
                continue
        match = re.search(r"(?:^|\s)mtu=(\d+)(?:\s|$)", event.strip())
        if match:
            mtu = int(match.group(1))
            if mtu >= 4:
                return mtu
    return None


def _parse_config(events: Sequence[Any]) -> Optional[str]:
    for event in events:
        if not isinstance(event, bytes):
            continue
        if len(event) in (14, 15, 16, 19):
            return "%02X" % event[7]
    return None


def _profile_for_driver(driver_id: str) -> ScreenProfile:
    profiles = {"03": FOUR_TWO_THREE_COLOR_PROFILE, "05": FOUR_TWO_PROFILE, "13": THREE_NINETY_EIGHT_PROFILE}
    try:
        return profiles[driver_id.upper()]
    except KeyError as exc:
        raise DeviceError("设备驱动 %s 不在已确认的安全配置表中" % driver_id) from exc


def _parse_slots(message: str, protocol_hint: Optional[str] = None) -> SlotInventory:
    parts = message.strip().split()
    if not parts or not parts[0].startswith("slots="):
        raise DeviceError("设备槽位通知格式无法识别")
    try:
        count = int(parts[0].split("=", 1)[1])
    except ValueError as exc:
        raise DeviceError("设备槽位数量无效") from exc
    if count <= 0 or count > 1024:
        raise DeviceError("设备槽位数量异常：%d" % count)
    values = parts[1:]
    dynamic = len(values) >= 5 and all(re.fullmatch(r"\d+", item or "") for item in values[:5])
    if dynamic:
        page_start, page_count, used_mask, selected, flash_size = (int(item) for item in values[:5])
        if page_count <= 0 or page_count > count:
            raise DeviceError("设备槽位分页数量异常")
        fingerprints = tuple(values[5 : 5 + page_count])
        protocol_version = "v2"
    else:
        try:
            used_mask = int(values[0], 0)
        except (IndexError, ValueError) as exc:
            raise DeviceError("设备槽位位图无效") from exc
        selected = None
        fingerprint_start = 1
        if len(values) > 1 and re.fullmatch(r"\d+", values[1]):
            selected = int(values[1])
            fingerprint_start = 2
        page_start, page_count, flash_size = 0, count, 0
        fingerprints = tuple(values[fingerprint_start : fingerprint_start + count])
        protocol_version = "legacy"
    if protocol_hint is not None and protocol_hint != protocol_version:
        raise DeviceError("设备槽位协议版本前后矛盾")
    used = tuple(index + page_start for index in range(page_count) if used_mask & (1 << index))
    return SlotInventory(count, used, selected, flash_size, fingerprints, page_start, page_count, protocol_version)


def _collect_text(events: Sequence[Any]) -> Optional[str]:
    for event in events:
        if isinstance(event, bytes):
            try:
                event = event.decode("ascii")
            except UnicodeDecodeError:
                continue
        if event.strip().startswith("slots="):
            return event.strip()
    return None


def _wait_until(events: List[Any], transport: Any, predicate: Callable[[Sequence[Any]], bool], timeout: float = 8.0) -> bool:
    """Drain delayed BLE notifications until a discovery predicate is met."""
    if predicate(events):
        return True
    waiter = getattr(transport, "wait_for_notification", None)
    if waiter is None:
        return False
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        value = waiter(timeout=remaining)
        if value is None:
            return False
        events.append(_decode_event(value))
        if predicate(events):
            return True


def discover_device(transport: Any, config: DeviceConfig) -> DeviceProfile:
    """Read version/configuration and issue only read-only discovery commands."""
    del config  # reserved for future identifier selection at the scan boundary
    try:
        version = bytes(transport.read_version())
    except Exception as exc:
        raise DeviceError("读取固件版本失败：%s" % exc) from exc
    events: List[Any] = []
    transport.start_notifications(lambda value: events.append(_decode_event(value)))
    transport.write(encode_command(CMD_GET_SLOTS), response=True)
    transport.write(encode_command(CMD_INIT), response=True)
    _wait_until(events, transport, lambda items: _parse_config(items) is not None and _collect_text(items) is not None)
    driver_id = _parse_config(events)
    if driver_id is None:
        raise DeviceError("未收到设备屏幕配置")
    screen = _profile_for_driver(driver_id)
    slots_message = _collect_text(events)
    if slots_message is None:
        raise DeviceError("未收到设备槽位状态")
    inventory = _parse_slots(slots_message)
    mtu = _parse_mtu(events) or 23
    protocol_version = inventory.protocol_version
    firmware_label = version.decode("utf-8", errors="replace").strip() or "unknown"
    return DeviceProfile(screen, driver_id, firmware_label, screen.width, screen.height, screen.color_mode, protocol_version, mtu)


def read_slot_inventory(transport: Any, profile: DeviceProfile) -> SlotInventory:
    """Query slot metadata; this never sends image, erase, or commit commands."""
    events: List[Any] = []
    transport.start_notifications(lambda value: events.append(_decode_event(value)))
    transport.write(encode_command(CMD_GET_SLOTS), response=True)
    _wait_until(events, transport, lambda items: _collect_text(items) is not None)
    message = _collect_text(events)
    if message is None:
        raise DeviceError("未收到设备槽位状态")
    return _parse_slots(message, protocol_hint=profile.protocol_version)
