"""Idempotent, slot-scoped synchronisation transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .clock import sync_clock
from .config import AppConfig
from .device import DeviceError, DeviceProfile, SlotInventory, discover_device, read_slot_inventory
from .feed import FeedError, DashboardFeed, load_feed
from .protocol import CMD_SET_SLOT, CMD_WRITE_IMG, encode_command, encode_slot_action, chunk_image
from .render import RenderedImage, render_combined_dashboard, render_dashboards
from .profiles import FOUR_TWO_PROFILE, FOUR_TWO_THREE_COLOR_PROFILE, THREE_NINETY_EIGHT_PROFILE
from .state import SyncState, default_state_path


class SyncError(RuntimeError):
    pass


@dataclass
class SlotResult:
    updated: bool = False
    skipped: bool = False
    failed: bool = False
    error: Optional[str] = None
    sha256: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.updated and not self.failed


@dataclass
class SyncResult:
    codex: SlotResult
    github: SlotResult
    skipped_reason: Optional[str] = None
    error_class: Optional[str] = None
    hashes: Dict[str, str] = None
    clock_synced: bool = False
    clock_error: Optional[str] = None


def _connect_transport(transport: Any, config: AppConfig) -> None:
    identifier = config.device.identifier
    if hasattr(transport, "scan"):
        devices = list(transport.scan(timeout=config.scan_timeout_seconds))
        if not devices:
            raise SyncError("未发现 EPD 设备")
        if identifier is None:
            selected = devices[0]
            identifier = getattr(selected, "address", None) or getattr(selected, "id", None) or str(selected)
    if not identifier:
        raise SyncError("未配置设备标识")
    if hasattr(transport, "connect"):
        transport.connect(identifier)


def _wait_ready(events: List[Any], transport: Any, timeout: float) -> bool:
    def is_ready(value: Any) -> bool:
        decoded = value.decode("ascii", errors="ignore") if isinstance(value, bytes) else str(value)
        return decoded.strip() == "ready=1"

    if any(is_ready(event) for event in events):
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
        events.append(value)
        if is_ready(value):
            return True


def write_slot_image(device: Any, slot: int, image: RenderedImage, activate: bool = False) -> None:
    transport = getattr(device, "transport", device)
    profile = getattr(device, "profile", device)
    protocol_v2 = getattr(profile, "protocol_version", "legacy") == "v2"
    mtu = int(getattr(profile, "mtu", 23))
    events: List[Any] = []
    if hasattr(transport, "start_notifications"):
        transport.start_notifications(lambda value: events.append(value))
    transport.write(encode_command(CMD_SET_SLOT, encode_slot_action(0, slot, protocol_v2)), response=True)
    if getattr(profile, "color_mode", "four_color") == "three_color":
        if len(image.packed) % 2:
            raise SyncError("三色图像数据长度不是两个等长平面")
        half = len(image.packed) // 2
        planes = ((image.packed[:half], "bw"), (image.packed[half:], "color"))
    else:
        planes = ((image.packed, "bw"),)
    for plane, step in planes:
        for chunk in chunk_image(plane, mtu=mtu, step=step):
            transport.write(encode_command(CMD_WRITE_IMG, chunk), response=True)
    # The device emits ready=1 only after the slot commit command.  Sending
    # commit after waiting leaves real panels waiting forever and can make a
    # successful image transfer look like a failed transaction.
    # The custom v2 firmware uses action=2 to commit a slot without selecting
    # it, and action=3 to commit and immediately display it.  The latter is
    # the same path as the backend's “保存后刷新” option.
    transport.write(encode_command(CMD_SET_SLOT, encode_slot_action(3 if activate else 2, slot, protocol_v2)), response=True)
    if not _wait_ready(events, transport, timeout=30.0):
        raise SyncError("未收到 ready=1，提交状态未确认")


def _result_pair() -> Dict[str, SlotResult]:
    return {"codex": SlotResult(), "github": SlotResult()}


def _require_fresh_account_usage(feed: DashboardFeed, now: datetime, max_age: timedelta) -> None:
    if feed.token_usage.source != "codex-app-server-account-usage":
        return
    observed_at = feed.token_usage.observed_at
    if observed_at is None or now - observed_at > max_age or now - observed_at < timedelta(seconds=-60):
        raise SyncError("Codex Token 数据已过期，拒绝写入设备")


def _require_writable_combined_feed(feed: DashboardFeed) -> None:
    warning_messages = {warning.message for warning in feed.warnings}
    if not feed.token_usage.available and "codex_token_usage_unavailable" in warning_messages and (
        feed.contribution_status == "ok" or "github_auto_select_unavailable" in warning_messages
    ):
        raise SyncError("Codex Token 数据不可用，拒绝写入设备")
    if feed.contribution_status == "unavailable" and "github_auto_select_unavailable" in warning_messages:
        if not feed.token_usage.available:
            raise SyncError("Codex Token 与 GitHub 数据不可用，拒绝写入设备")
        raise SyncError("GitHub 数据不可用，拒绝写入设备")


def sync_once(config: AppConfig, transport_factory: Any, now: datetime, state_path: Optional[Path] = None, only: Optional[Sequence[str]] = None, combined: bool = False) -> SyncResult:
    results = _result_pair()
    selected_kinds = tuple(only) if only is not None else ("codex", "github")
    if not selected_kinds or any(kind not in ("codex", "github") for kind in selected_kinds):
        raise ValueError("only 只能包含 codex 或 github")
    if combined and "github" in selected_kinds:
        raise ValueError("合并看板只能写入 Codex 槽位；请使用 --only codex")
    for kind in ("codex", "github"):
        if kind not in selected_kinds:
            results[kind].skipped = True
    clock_synced = False
    clock_error: Optional[str] = None
    if config.clock.enabled:
        try:
            sync_clock(config.clock.identifier or "", now)
            clock_synced = True
        except Exception as exc:
            clock_error = str(exc)
    clock_fields = {"clock_synced": clock_synced, "clock_error": clock_error}
    if not config.slots.configured:
        results["codex"].skipped = True
        results["github"].skipped = True
        return SyncResult(results["codex"], results["github"], skipped_reason="slots_not_configured", hashes={}, **clock_fields)
    state = SyncState.load(state_path or default_state_path())
    try:
        feed = load_feed(
            config.feed_path,
            now,
            timedelta(seconds=config.stale_after_seconds),
            local_snapshot_path=config.local_snapshot_output,
            local_stale_after=timedelta(seconds=config.local_stale_after_seconds),
        )
        if "codex" in selected_kinds:
            _require_fresh_account_usage(feed, now, timedelta(seconds=config.local_stale_after_seconds))
        if combined:
            _require_writable_combined_feed(feed)
        # Hash both confirmed layouts before touching BLE. This preserves the
        # no-scan fast path without persisting a guessed screen profile.
        candidate_hashes = []
        for candidate in (FOUR_TWO_PROFILE, FOUR_TWO_THREE_COLOR_PROFILE, THREE_NINETY_EIGHT_PROFILE):
            if combined:
                combined_rendered = render_combined_dashboard(feed, candidate)
                candidate_hashes.append({"codex": combined_rendered.sha256, "github": state.hashes.get("github", "")})
            else:
                candidate_rendered = render_dashboards(feed, candidate)
                candidate_hashes.append({kind: candidate_rendered[kind].sha256 for kind in ("codex", "github")})
        unchanged_hashes = next((item for item in candidate_hashes if all(state.hashes.get(kind) == item[kind] for kind in selected_kinds)), None)
        if unchanged_hashes is not None:
            results["codex"].skipped = True
            results["github"].skipped = True
            return SyncResult(results["codex"], results["github"], skipped_reason="unchanged", hashes=unchanged_hashes, **clock_fields)

        transport = transport_factory()
        _connect_transport(transport, config)
        profile = discover_device(transport, config.device)
        if combined:
            combined_rendered = render_combined_dashboard(feed, profile.screen_profile)
            rendered = {"codex": combined_rendered}
            hashes = {"codex": combined_rendered.sha256, "github": state.hashes.get("github", "")}
        else:
            rendered = render_dashboards(feed, profile.screen_profile)
            hashes = {kind: rendered[kind].sha256 for kind in ("codex", "github")}
        inventory = read_slot_inventory(transport, profile)
        for kind, slot in (("codex", config.slots.codex), ("github", config.slots.github)):
            if kind not in selected_kinds:
                continue
            result = results[kind]
            result.sha256 = hashes[kind]
            if state.hashes.get(kind) == hashes[kind] and slot in inventory.used_slots:
                result.skipped = True
                continue
            if slot < 0 or slot >= inventory.count or slot in config.slots.protected:
                raise SyncError("%s 槽位不在可写范围" % kind)
            try:
                write_slot_image(
                    type("Session", (), {"transport": transport, "profile": profile})(),
                    slot,
                    rendered[kind],
                    activate=config.activate_after_sync and kind == "codex",
                )
                verified = read_slot_inventory(transport, profile)
                if slot not in verified.used_slots:
                    raise SyncError("%s 槽位提交后未在设备状态中出现" % kind)
                result.updated = True
                state.hashes[kind] = hashes[kind]
                state.last_success_at = now
                state.last_result = "success" if combined or kind == "github" else "partial"
                state.save(state_path or default_state_path())
            except Exception as exc:
                result.failed = True
                result.error = str(exc)
        state.device_identifier = config.device.identifier
        state.consecutive_failures = 1 if any(item.failed for item in results.values()) else 0
        state.save(state_path or default_state_path())
        return SyncResult(results["codex"], results["github"], hashes=hashes, error_class="transfer" if any(item.failed for item in results.values()) else None, **clock_fields)
    except (FeedError, DeviceError, SyncError) as exc:
        state.consecutive_failures += 1
        state.last_result = "failed"
        state.save(state_path or default_state_path())
        for kind in selected_kinds:
            results[kind].failed = True
            results[kind].error = str(exc)
        return SyncResult(results["codex"], results["github"], error_class=exc.__class__.__name__, **clock_fields)
    finally:
        try:
            if "transport" in locals() and hasattr(transport, "disconnect"):
                transport.disconnect()
        except Exception:
            pass
