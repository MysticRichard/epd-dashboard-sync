from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .feed import FeedError


def _profile_int(value: Any, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FeedError("%s 必须是非负整数或 null" % name)
    return value


@dataclass(frozen=True)
class ProfileTokenUsage:
    last_7_days: Optional[int]
    total: Optional[int]
    observed_at: Optional[str]


def parse_profile_usage(raw: Any) -> ProfileTokenUsage:
    if raw is None:
        return ProfileTokenUsage(None, None, None)
    if not isinstance(raw, dict):
        raise FeedError("codex.profileUsage 必须是对象")
    last_7_days = _profile_int(raw.get("last7DaysTokens"), "codex.profileUsage.last7DaysTokens")
    total = _profile_int(raw.get("totalTokens"), "codex.profileUsage.totalTokens")
    observed_at = raw.get("observedAt")
    if observed_at is not None and not isinstance(observed_at, str):
        raise FeedError("codex.profileUsage.observedAt 必须是字符串或 null")
    return ProfileTokenUsage(last_7_days, total, observed_at)
