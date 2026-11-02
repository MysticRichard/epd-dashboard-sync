"""Command-line entry point for rendering, discovery, and one-shot sync."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import load_config
from .codex_local import collect_account_codex, collect_local_codex, write_snapshot_atomic
from .device import DeviceError, discover_device, read_slot_inventory
from .feed import load_feed
from .launchagent import CliError, install_launch_agent, uninstall_launch_agent
from .profiles import FOUR_TWO_PROFILE, FOUR_TWO_THREE_COLOR_PROFILE, PROFILES, THREE_NINETY_EIGHT_PROFILE
from .render import render_dashboards
from .state import SyncState, default_state_path
from .sync import sync_once
from .transport import BleakTransport


ALIASES = {
    "4.2_400_300": FOUR_TWO_PROFILE,
    "4.2_400x300_four_color": FOUR_TWO_PROFILE,
    "4.2_400_300_three_color": FOUR_TWO_THREE_COLOR_PROFILE,
    "3.98_768_552": THREE_NINETY_EIGHT_PROFILE,
    "3.98_768x552_four_color": THREE_NINETY_EIGHT_PROFILE,
}


def _fresh_account_snapshot(path: Path, now: datetime, max_age: timedelta) -> Optional[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        token_usage = payload["codex"]["tokenUsage"]
        generated_at = datetime.fromisoformat(payload["generatedAt"].replace("Z", "+00:00"))
        age = now - generated_at
        if token_usage.get("source") != "codex-app-server-account-usage" or age < timedelta(seconds=-60) or age > max_age:
            return None
        return payload
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _profile(name: str):
    if name in ALIASES:
        return ALIASES[name]
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise CliError("未知屏幕 profile：%s" % name) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epd-dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render")
    render.add_argument("--feed", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    render.add_argument("--profile", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--config", required=True, type=Path)
    sync = sub.add_parser("sync-once")
    sync.add_argument("--config", required=True, type=Path)
    sync.add_argument("--only", choices=("codex", "github"), action="append", help="仅同步指定看板，可重复指定")
    sync.add_argument("--combined", action="store_true", help="将 Codex 与 GitHub 合并写入 Codex 槽位（需配合 --only codex）")
    install = sub.add_parser("install-autosync")
    install.add_argument("--config", required=True, type=Path)
    install.add_argument("--executable", type=Path, default=Path("epd-dashboard"))
    sub.add_parser("uninstall-autosync")
    sub.add_parser("status")
    snapshot = sub.add_parser("codex-snapshot")
    snapshot.add_argument("--config", required=True, type=Path)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "render":
            now = datetime.now().astimezone()
            feed = load_feed(args.feed, now, __import__("datetime").timedelta(days=3650))
            result = render_dashboards(feed, _profile(args.profile), args.output)
            for kind in ("codex", "github"):
                print("%s %s" % (kind, result[kind].sha256))
            return 0
        if args.command == "discover":
            config = load_config(args.config)
            transport = BleakTransport(config.device.service_uuid)
            devices = list(transport.scan(timeout=config.scan_timeout_seconds))
            if not devices:
                raise CliError("未发现 EPD 设备；请将设备唤醒并放在 Mac 附近")
            identifier = config.device.identifier
            if not identifier:
                selected = devices[0]
                identifier = getattr(selected, "address", None) or getattr(selected, "id", None) or str(selected)
            transport.connect(identifier)
            profile = discover_device(transport, config.device)
            inventory = read_slot_inventory(transport, profile)
            print("%s %dx%d %s %s MTU=%d slots=%d used=%s selected=%s" % (
                profile.driver_id, profile.width, profile.height, profile.color_mode,
                profile.protocol_version, profile.mtu, inventory.count,
                ",".join(str(slot) for slot in inventory.used_slots) or "none",
                "none" if inventory.selected_slot is None else inventory.selected_slot,
            ))
            transport.disconnect()
            return 0
        if args.command == "sync-once":
            config = load_config(args.config)
            result = sync_once(config, lambda: BleakTransport(config.device.service_uuid), datetime.now().astimezone(), only=args.only, combined=args.combined)
            for kind in ("codex", "github"):
                slot_result = getattr(result, kind)
                if slot_result.failed:
                    print("%s failed: %s" % (kind, slot_result.error or "unknown error"))
                elif slot_result.updated:
                    print("%s updated" % kind)
                elif slot_result.skipped:
                    print("%s skipped" % kind)
            skipped_reason = getattr(result, "skipped_reason", None)
            error_class = getattr(result, "error_class", None)
            if skipped_reason:
                print("skipped_reason=%s" % skipped_reason)
            if getattr(result, "clock_synced", False):
                print("clock synced")
            elif getattr(result, "clock_error", None):
                print("clock failed: %s" % result.clock_error)
            if error_class:
                print("error_class=%s" % error_class)
            return 1 if error_class else 0
        if args.command == "install-autosync":
            path = install_launch_agent(args.config, args.executable)
            print(path)
            return 0
        if args.command == "uninstall-autosync":
            print(uninstall_launch_agent())
            return 0
        if args.command == "status":
            state = SyncState.load(default_state_path())
            print("last_result=%s consecutive_failures=%d" % (state.last_result or "none", state.consecutive_failures))
            return 0
        if args.command == "codex-snapshot":
            config = load_config(args.config)
            now = datetime.now().astimezone()
            local_snapshot = collect_local_codex(
                config.codex_home,
                now=now,
                stale_after=__import__("datetime").timedelta(seconds=config.local_stale_after_seconds),
            )
            account_snapshot = collect_account_codex(config.codex_home, now=now)
            if account_snapshot is None:
                # App-server startup can briefly race with an existing Codex process.
                account_snapshot = collect_account_codex(config.codex_home, now=now)
            if account_snapshot is None:
                previous = _fresh_account_snapshot(
                    config.local_snapshot_output,
                    now,
                    timedelta(seconds=config.local_stale_after_seconds),
                )
                if previous is not None:
                    print("codex snapshot preserved: account usage unavailable; previous account snapshot is still fresh")
                    return 0
                raise CliError("Codex account usage unavailable; refusing to write local session counters")
            snapshot = account_snapshot
            fresh_local_windows = {
                duration: window
                for duration, window in local_snapshot.windows.items()
                if now - window.observed_at <= timedelta(seconds=config.local_stale_after_seconds)
            }
            if fresh_local_windows and not snapshot.windows:
                snapshot = replace(account_snapshot, windows=fresh_local_windows)
            write_snapshot_atomic(snapshot, config.local_snapshot_output, generated_at=now)
            print("codex snapshot written: available=%s total=%s week_to_date=%s stale=%s warnings=%s" % (
                snapshot.available,
                snapshot.total if snapshot.total is not None else "--",
                snapshot.last_7_days if snapshot.last_7_days is not None else "--",
                snapshot.stale,
                ",".join(snapshot.warnings) or "none",
            ))
            return 0
    except (CliError, DeviceError, ValueError, OSError, RuntimeError) as exc:
        print("error: %s" % exc)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
