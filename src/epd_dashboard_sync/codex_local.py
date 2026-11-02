"""Read-only collector for Codex rollout JSONL files.

Only usage counters and rate-limit metadata are retained.  This module never
opens the Codex credential store and deliberately drops unknown payload fields.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class LocalQuotaWindow:
    used_percent: float
    window_duration_minutes: int
    resets_at: Optional[datetime]
    observed_at: datetime


@dataclass(frozen=True)
class LocalCodexSnapshot:
    available: bool
    total: Optional[int]
    last_7_days: Optional[int]
    windows: Dict[int, LocalQuotaWindow]
    observed_at: Optional[datetime]
    source: str = "local-session-log"
    stale: bool = False
    warnings: tuple[str, ...] = ()
    peak_daily: Optional[int] = None

    def as_dict(self, generated_at: Optional[datetime] = None) -> dict[str, Any]:
        generated = generated_at or self.observed_at or datetime.now().astimezone()
        usage = {
            "available": self.available,
            "total": self.total,
            "last7Days": self.last_7_days,
            "peakDaily": self.peak_daily,
            "observedAt": self.observed_at.isoformat() if self.observed_at else None,
            "source": self.source,
        }
        windows = [
            {
                "durationMinutes": duration,
                "usedPercent": window.used_percent,
                "resetsAt": window.resets_at.isoformat() if window.resets_at else None,
                "source": self.source,
                "observedAt": window.observed_at.isoformat(),
            }
            for duration, window in sorted(self.windows.items())
        ]
        return {
            "schemaVersion": 1,
            "generatedAt": generated.isoformat(),
            "codex": {"tokenUsage": usage, "windows": windows, "dataWarnings": list(self.warnings)},
        }


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _counter(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    number = int(value)
    return number if number >= 0 else None


def _usage_counter(value: Any) -> Optional[int]:
    """Return an explicit token total from either legacy or current shapes."""
    if isinstance(value, dict):
        value = _first(value, "total_tokens", "totalTokens")
    return _counter(value)


def _rollout_files(home: Path) -> Iterable[Path]:
    for directory in (home / "sessions", home / "archived_sessions"):
        if directory.exists():
            yield from sorted(directory.rglob("*.jsonl"))


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _usage_from_event(event: dict[str, Any]) -> Optional[tuple[int, int]]:
    for mapping in _walk_dicts(event):
        if "token_count" in mapping or "tokenCount" in mapping:
            raw = _first(mapping, "token_count", "tokenCount")
            if not isinstance(raw, dict):
                return None
            total = _first(raw, "total_token_usage", "totalTokenUsage", "total")
            last = _first(raw, "last_token_usage", "lastTokenUsage", "last")
            total_value, last_value = _usage_counter(total), _usage_counter(last)
            if total_value is None or last_value is None:
                return None
            return total_value, last_value
        if "total_token_usage" in mapping or "totalTokenUsage" in mapping:
            total = _first(mapping, "total_token_usage", "totalTokenUsage")
            last = _first(mapping, "last_token_usage", "lastTokenUsage")
            total_value, last_value = _usage_counter(total), _usage_counter(last)
            if total_value is None or last_value is None:
                return None
            return total_value, last_value
    return None


def _has_usage_shape(event: dict[str, Any]) -> bool:
    return any(
        "token_count" in mapping
        or "tokenCount" in mapping
        or "total_token_usage" in mapping
        or "totalTokenUsage" in mapping
        for mapping in _walk_dicts(event)
    )


def _has_current_usage_shape(event: dict[str, Any]) -> bool:
    for mapping in _walk_dicts(event):
        info = mapping.get("info")
        if not isinstance(info, dict):
            continue
        if ("total_token_usage" in info or "totalTokenUsage" in info) and (
            "last_token_usage" in info or "lastTokenUsage" in info
        ):
            return True
    return False


def _usage_bucket_total(items: Any, start: datetime, end: datetime) -> Optional[int]:
    if not isinstance(items, list):
        return None
    total = 0
    found = False
    for item in items:
        if not isinstance(item, dict):
            continue
        day = _first(item, "startDate", "start_date")
        tokens = _counter(_first(item, "tokens", "totalTokens", "total_tokens"))
        if not isinstance(day, str) or tokens is None:
            continue
        try:
            bucket_day = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if start.date() <= bucket_day <= end.date():
            total += tokens
            found = True
    return total if found else None


def _usage_bucket_peak(items: Any) -> Optional[int]:
    if not isinstance(items, list):
        return None
    values = [
        tokens
        for item in items
        if isinstance(item, dict)
        for tokens in [_counter(_first(item, "tokens", "totalTokens", "total_tokens"))]
        if tokens is not None
    ]
    return max(values) if values else None


def snapshot_from_account_usage(response: dict[str, Any], *, now: datetime) -> LocalCodexSnapshot:
    summary = response.get("summary") if isinstance(response, dict) else None
    if not isinstance(summary, dict):
        summary = {}
    lifetime = _counter(_first(summary, "lifetimeTokens", "lifetime_tokens"))
    local_now = now.astimezone()
    # The dashboard labels this value as the provider's current week, which
    # starts on Sunday and runs through the observation date.
    days_since_sunday = (local_now.weekday() + 1) % 7
    week_start = local_now - timedelta(days=days_since_sunday)
    # Daily buckets are authoritative for this window; the weekly bucket is
    # only a fallback when daily buckets are unavailable.
    weekly = _usage_bucket_total(
        _first(response, "dailyUsageBuckets", "daily_usage_buckets"),
        week_start,
        local_now,
    )
    if weekly is None:
        weekly = _usage_bucket_total(
            _first(response, "weeklyUsageBuckets", "weekly_usage_buckets"),
            week_start,
            local_now,
        )
    peak_daily = _usage_bucket_peak(_first(response, "dailyUsageBuckets", "daily_usage_buckets"))
    available = lifetime is not None or weekly is not None or peak_daily is not None
    return LocalCodexSnapshot(
        available=available,
        total=lifetime,
        last_7_days=weekly,
        windows={},
        observed_at=now,
        source="codex-app-server-account-usage",
        warnings=() if available else ("account_usage_unavailable",),
        peak_daily=peak_daily,
    )


def windows_from_rate_limits(response: dict[str, Any], *, now: datetime) -> Dict[int, LocalQuotaWindow]:
    raw_limits = response.get("rateLimits") if isinstance(response, dict) else None
    if not isinstance(raw_limits, dict):
        return {}
    windows: Dict[int, LocalQuotaWindow] = {}
    for name in ("primary", "secondary"):
        raw = raw_limits.get(name)
        if not isinstance(raw, dict):
            continue
        duration = _first(raw, "windowDurationMins", "window_duration_mins")
        used = _first(raw, "usedPercent", "used_percent")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            continue
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(float(used)):
            continue
        duration_int = int(duration)
        if duration_int not in (300, 10080) or not 0 <= float(used) <= 100:
            continue
        windows[duration_int] = LocalQuotaWindow(
            float(used),
            duration_int,
            _timestamp(_first(raw, "resetsAt", "resets_at")),
            now,
        )
    return windows


def collect_account_codex(home: Path, *, now: datetime, executable: str = "codex") -> Optional[LocalCodexSnapshot]:
    """Read account totals through Codex's own app-server, without opening auth files."""
    request = (
        {"method": "initialize", "id": 1, "params": {"clientInfo": {"name": "epd-dashboard-sync", "title": "EPD Dashboard Sync", "version": "0.1.0"}, "capabilities": {}}},
        {"method": "initialized", "params": {}},
        {"method": "account/usage/read", "id": 2, "params": {}},
        {"method": "account/rateLimits/read", "id": 3, "params": {}},
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(Path(home).expanduser())
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if process.stdin is None or process.stdout is None:
            return None
        process.stdin.write("\n".join(json.dumps(item, separators=(",", ":")) for item in request) + "\n")
        process.stdin.flush()
        messages: Queue[Optional[str]] = Queue()

        def read_output() -> None:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                messages.put(line)
            messages.put(None)

        threading.Thread(target=read_output, daemon=True).start()
        usage_result: Optional[dict[str, Any]] = None
        rate_limits_result: Optional[dict[str, Any]] = None
        rate_limits_seen = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=0.2)
            except Empty:
                continue
            if line is None:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == 2 and isinstance(message.get("result"), dict):
                usage_result = message["result"]
            if message.get("id") == 3:
                rate_limits_seen = True
                if isinstance(message.get("result"), dict):
                    rate_limits_result = message["result"]
            if usage_result is not None and rate_limits_seen:
                break
        if usage_result is not None:
            snapshot = snapshot_from_account_usage(usage_result, now=now)
            if rate_limits_result is not None:
                snapshot = replace(
                    snapshot,
                    windows=windows_from_rate_limits(rate_limits_result, now=now),
                )
            return snapshot
    except (OSError, subprocess.SubprocessError, BrokenPipeError):
        return None
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    return None


def _rate_limits_from_event(event: dict[str, Any], observed_at: datetime) -> Iterable[LocalQuotaWindow]:
    for mapping in _walk_dicts(event):
        raw_limits = _first(mapping, "rate_limits", "rateLimits")
        if not isinstance(raw_limits, dict):
            continue
        for raw in _walk_dicts(raw_limits):
            duration = _first(raw, "window_duration_mins", "windowDurationMins", "window_minutes", "durationMinutes")
            used = _first(raw, "used_percent", "usedPercent")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                continue
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(float(used)):
                continue
            duration_int = int(duration)
            if duration_int not in (300, 10080) or not 0 <= float(used) <= 100:
                continue
            reset = _timestamp(_first(raw, "resets_at", "resetsAt"))
            yield LocalQuotaWindow(float(used), duration_int, reset, observed_at)


def collect_local_codex(
    home: Path,
    *,
    now: datetime,
    lookback: timedelta = timedelta(days=7),
    stale_after: timedelta = timedelta(minutes=15),
) -> LocalCodexSnapshot:
    home = Path(home).expanduser()
    files = list(_rollout_files(home))
    if not files:
        return LocalCodexSnapshot(False, None, None, {}, None, warnings=("no_rollouts",))

    cutoff = now - lookback
    totals: list[int] = []
    current_usage_total = 0
    recent_usage_total = 0
    seen_usage_keys: set[tuple[str, str]] = set()
    windows: dict[int, LocalQuotaWindow] = {}
    observed: Optional[datetime] = None
    invalid = False

    for path in files:
        rollout_total: Optional[int] = None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            invalid = True
            continue
        for line_number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                invalid = True
                continue
            if not isinstance(event, dict):
                continue
            usage = _usage_from_event(event)
            if usage is None and _has_usage_shape(event):
                invalid = True
            timestamp = _timestamp(_first(event, "timestamp", "created_at", "createdAt"))
            if timestamp is None:
                continue
            observed = max(observed, timestamp) if observed else timestamp
            if usage is not None:
                total_value, last_value = usage
                current_shape = _has_current_usage_shape(event)
                event_id = _first(event, "id", "event_id", "eventId")
                dedup_key = (
                    str(path),
                    str(event_id) if event_id is not None else "%s:%s:%s" % (timestamp.isoformat(), total_value, last_value),
                )
                if dedup_key not in seen_usage_keys:
                    seen_usage_keys.add(dedup_key)
                    if current_shape:
                        current_usage_total += last_value
                    else:
                        rollout_total = total_value
                    if timestamp >= cutoff:
                        recent_usage_total += last_value
            for window in _rate_limits_from_event(event, timestamp):
                prior = windows.get(window.window_duration_minutes)
                if prior is None or window.observed_at >= prior.observed_at:
                    windows[window.window_duration_minutes] = window
        if rollout_total is not None:
            totals.append(rollout_total)

    warnings: list[str] = []
    if invalid:
        warnings.append("invalid_token_count")
    if not totals and not current_usage_total and not windows:
        warnings.append("no_valid_data")
    stale = observed is None or now - observed > stale_after
    if stale:
        warnings.append("stale_local_data")
    available = bool(totals or current_usage_total or windows)
    total_value = current_usage_total + (sum(totals) if totals else 0)
    return LocalCodexSnapshot(
        available=available,
        total=total_value if total_value else None,
        last_7_days=recent_usage_total if total_value else None,
        windows=windows,
        observed_at=observed,
        stale=stale,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def write_snapshot_atomic(snapshot: LocalCodexSnapshot, output: Path, *, generated_at: Optional[datetime] = None) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".%s.tmp" % output.name)
    temporary.write_text(json.dumps(snapshot.as_dict(generated_at), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, output)
