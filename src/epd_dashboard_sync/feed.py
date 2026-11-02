from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional



class FeedError(ValueError):
    """Raised when a dashboard snapshot is invalid or unsafe to render."""


@dataclass(frozen=True)
class QuotaWindow:
    remaining_percent: float
    resets_at: Optional[datetime]


@dataclass(frozen=True)
class QuotaData:
    five_hour: Optional[QuotaWindow]
    weekly: Optional[QuotaWindow]


@dataclass(frozen=True)
class TokenWindow:
    used: int
    limit: int


@dataclass(frozen=True)
class TokenUsage:
    available: bool
    five_hour: Optional[TokenWindow]
    weekly: Optional[TokenWindow]
    total: Optional[int] = None
    last_7_days: Optional[int] = None
    observed_at: Optional[datetime] = None
    source: Optional[str] = None
    peak_daily: Optional[int] = None


@dataclass(frozen=True)
class ContributionDay:
    date: str
    count: int


@dataclass(frozen=True)
class WorkflowStatus:
    status: Optional[str]
    conclusion: Optional[str]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class RepositoryStatus:
    full_name: str
    private: bool
    default_branch: str
    open_issues: Optional[int]
    open_pull_requests: Optional[int]
    latest_commit_at: Optional[datetime]
    workflow: WorkflowStatus
    commit_count: Optional[int] = None


@dataclass(frozen=True)
class FeedWarning:
    message: str


@dataclass(frozen=True)
class DashboardFeed:
    schema_version: int
    generated_at: datetime
    quota: QuotaData
    repositories: List[RepositoryStatus]
    warnings: List[FeedWarning]
    token_usage: TokenUsage = field(default_factory=lambda: TokenUsage(False, None, None))
    contributions: List[ContributionDay] = field(default_factory=list)
    contribution_total: Optional[int] = None
    contribution_status: str = "unavailable"
    profile_usage: "ProfileTokenUsage" = field(default_factory=lambda: _empty_profile_usage())


def _empty_profile_usage():
    from .profile import ProfileTokenUsage
    return ProfileTokenUsage(None, None, None)


def _timestamp(value: Any, name: str, required: bool = True) -> Optional[datetime]:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FeedError("%s 必须是带时区的 ISO 8601 时间" % name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedError("%s 不是有效 ISO 8601 时间" % name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedError("%s 必须包含时区" % name)
    return parsed


def _quota_window(raw: Any, name: str) -> Optional[QuotaWindow]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FeedError("quota.%s 必须是对象或 null" % name)
    remaining = raw.get("remainingPercent")
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
        raise FeedError("quota.%s.remainingPercent 必须是数字" % name)
    if not 0 <= float(remaining) <= 100:
        raise FeedError("quota.%s.remainingPercent 必须在 0 到 100 之间" % name)
    return QuotaWindow(
        remaining_percent=float(remaining),
        resets_at=_timestamp(raw.get("resetsAt"), "quota.%s.resetsAt" % name, required=False),
    )


def _nonnegative_int(value: Any, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FeedError("%s 必须是非负整数或 null" % name)
    return value


def _repository(raw: Any, index: int) -> RepositoryStatus:
    prefix = "repositories[%d]" % index
    if not isinstance(raw, dict):
        raise FeedError("%s 必须是对象" % prefix)
    full_name = raw.get("fullName")
    if not isinstance(full_name, str) or not full_name.strip() or "/" not in full_name:
        raise FeedError("%s.fullName 必须是 owner/name" % prefix)
    private = raw.get("private")
    if not isinstance(private, bool):
        raise FeedError("%s.private 必须是布尔值" % prefix)
    default_branch = raw.get("defaultBranch")
    if not isinstance(default_branch, str) or not default_branch.strip():
        raise FeedError("%s.defaultBranch 必须是非空字符串" % prefix)
    workflow_raw = raw.get("workflow")
    if workflow_raw is None:
        workflow_raw = {}
    if not isinstance(workflow_raw, dict):
        raise FeedError("%s.workflow 必须是对象或省略" % prefix)
    return RepositoryStatus(
        full_name=full_name.strip(),
        private=private,
        default_branch=default_branch.strip(),
        open_issues=_nonnegative_int(raw.get("openIssues"), "%s.openIssues" % prefix),
        open_pull_requests=_nonnegative_int(raw.get("openPullRequests"), "%s.openPullRequests" % prefix),
        latest_commit_at=_timestamp(raw.get("latestCommitAt"), "%s.latestCommitAt" % prefix, required=False),
        workflow=WorkflowStatus(
            status=workflow_raw.get("status") if isinstance(workflow_raw.get("status"), str) else None,
            conclusion=workflow_raw.get("conclusion") if isinstance(workflow_raw.get("conclusion"), str) else None,
            updated_at=_timestamp(workflow_raw.get("updatedAt"), "%s.workflow.updatedAt" % prefix, required=False),
        ),
        commit_count=_nonnegative_int(raw.get("commitCount"), "%s.commitCount" % prefix),
    )


def _token_window(raw: Any, name: str) -> Optional[TokenWindow]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FeedError("%s 必须是对象或 null" % name)
    used = _nonnegative_int(raw.get("used"), "%s.used" % name)
    limit = _nonnegative_int(raw.get("limit"), "%s.limit" % name)
    if used is None or limit is None:
        raise FeedError("%s 必须同时包含 used 和 limit" % name)
    if used > limit:
        raise FeedError("%s.used 不能大于 limit" % name)
    return TokenWindow(used=used, limit=limit)


def _token_usage(raw: Any) -> TokenUsage:
    if raw is None:
        return TokenUsage(False, None, None)
    if not isinstance(raw, dict):
        raise FeedError("codex.tokenUsage 必须是对象")
    five = _token_window(raw.get("fiveHour"), "codex.tokenUsage.fiveHour")
    weekly = _token_window(raw.get("weekly"), "codex.tokenUsage.weekly")
    total = _nonnegative_int(raw.get("total"), "codex.tokenUsage.total")
    last_7_days = _nonnegative_int(raw.get("last7Days"), "codex.tokenUsage.last7Days")
    peak_daily = _nonnegative_int(raw.get("peakDaily"), "codex.tokenUsage.peakDaily")
    observed_at = _timestamp(raw.get("observedAt"), "codex.tokenUsage.observedAt", required=False)
    source = raw.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise FeedError("codex.tokenUsage.source 必须是非空字符串或 null")
    available = raw.get("available", bool(five or weekly))
    if not isinstance(available, bool):
        raise FeedError("codex.tokenUsage.available 必须是布尔值")
    if available and not (five or weekly or total is not None or last_7_days is not None or peak_daily is not None):
        raise FeedError("codex.tokenUsage.available 为 true 时必须包含 token 数据")
    return TokenUsage(available=available, five_hour=five, weekly=weekly, total=total, last_7_days=last_7_days, observed_at=observed_at, source=source, peak_daily=peak_daily)


def _duration_quota(raw: Any, name: str) -> Optional[QuotaWindow]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FeedError("%s 必须是对象" % name)
    used = raw.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= float(used) <= 100:
        raise FeedError("%s.usedPercent 必须在 0 到 100 之间" % name)
    return QuotaWindow(100.0 - float(used), _timestamp(raw.get("resetsAt"), name + ".resetsAt", required=False))


def _windows(raw: Any) -> dict[int, QuotaWindow]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise FeedError("codex.windows 必须是数组")
    result: dict[int, QuotaWindow] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise FeedError("codex.windows[%d] 必须是对象" % index)
        duration = item.get("durationMinutes")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration not in (300, 10080):
            continue
        result[duration] = _duration_quota(item, "codex.windows[%d]" % index)  # type: ignore[assignment]
    return result


def _contributions(raw: Any) -> List[ContributionDay]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FeedError("github.contributions.lastYear 必须是数组")
    if len(raw) > 366:
        raise FeedError("github.contributions.lastYear 不能超过 366 天")
    result: List[ContributionDay] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("date"), str):
            raise FeedError("github.contributions.lastYear[%d] 必须包含日期" % index)
        date = item["date"]
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise FeedError("github.contributions.lastYear[%d].date 无效" % index) from exc
        count = _nonnegative_int(item.get("count"), "github.contributions.lastYear[%d].count" % index)
        if count is None:
            raise FeedError("github.contributions.lastYear[%d].count 不能为空" % index)
        result.append(ContributionDay(date=date, count=count))
    return result


def _read_local_codex_snapshot(path: Optional[Path], now: datetime, stale_after: timedelta) -> tuple[Optional[TokenUsage], dict[int, QuotaWindow]]:
    """Read a fresh host-local Codex snapshot without changing the NAS feed."""
    if path is None:
        return None, {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None, {}
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        return None, {}
    generated_value = raw.get("generatedAt")
    if not isinstance(generated_value, str):
        return None, {}
    try:
        generated_at = _timestamp(generated_value, "codex local generatedAt")
    except FeedError:
        return None, {}
    age = now - generated_at
    if age < timedelta(seconds=-60) or age > stale_after:
        return None, {}
    codex_raw = raw.get("codex") if isinstance(raw.get("codex"), dict) else {}
    usage = _token_usage(codex_raw.get("tokenUsage"))
    windows = _windows(codex_raw.get("windows"))
    return usage, windows


def load_feed(
    path: Path,
    now: datetime,
    stale_after: timedelta,
    *,
    local_snapshot_path: Optional[Path] = None,
    local_stale_after: Optional[timedelta] = None,
) -> DashboardFeed:
    from .profile import ProfileTokenUsage, parse_profile_usage
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeedError("看板快照不存在：%s" % path) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeedError("看板快照无法读取：%s" % exc) from exc
    if not isinstance(raw, dict):
        raise FeedError("看板快照根节点必须是对象")
    if raw.get("schemaVersion") != 1:
        raise FeedError("不支持的看板 schemaVersion")
    generated_at = _timestamp(raw.get("generatedAt"), "generatedAt")
    if now.tzinfo is None or now.utcoffset() is None:
        raise FeedError("now 必须包含时区")
    if stale_after.total_seconds() <= 0:
        raise FeedError("stale_after 必须是正时长")
    age = now - generated_at
    if age > stale_after:
        raise FeedError("看板快照已过期")
    if age < timedelta(seconds=-60):
        raise FeedError("看板快照时间异常")

    quota_raw = raw.get("quota")
    if quota_raw is None:
        quota_raw = {}
    if not isinstance(quota_raw, dict):
        raise FeedError("quota 必须是对象")
    github_raw = raw.get("github") if isinstance(raw.get("github"), dict) else {}
    repositories_raw = raw.get("repositories")
    if (repositories_raw is None or repositories_raw == []) and isinstance(github_raw.get("repositories"), list):
        repositories_raw = github_raw.get("repositories")
    if not isinstance(repositories_raw, list) or len(repositories_raw) > 5:
        raise FeedError("repositories 必须包含 0 到 5 个仓库")
    warnings_raw = raw.get("dataWarnings", [])
    if not isinstance(warnings_raw, list) or not all(isinstance(item, str) for item in warnings_raw):
        raise FeedError("dataWarnings 必须是字符串数组")

    codex_raw = raw.get("codex") if isinstance(raw.get("codex"), dict) else {}
    contributions_raw = github_raw.get("contributions") if isinstance(github_raw.get("contributions"), dict) else {}
    contribution_status = github_raw.get("contributionStatus", "unavailable")
    if contribution_status not in {"ok", "empty", "private_hidden", "private_unverified", "unavailable"}:
        raise FeedError("github.contributionStatus 无效")
    contribution_warnings = github_raw.get("contributionWarnings", [])
    if not isinstance(contribution_warnings, list) or not all(isinstance(item, str) for item in contribution_warnings):
        raise FeedError("github.contributionWarnings 必须是字符串数组")
    warning_messages = list(warnings_raw)
    warning_messages.extend(contribution_warnings)
    token_usage = _token_usage(codex_raw.get("tokenUsage"))
    duration_windows = _windows(codex_raw.get("windows"))
    five_raw = quota_raw.get("fiveHour")
    weekly_raw = quota_raw.get("weekly")
    if five_raw is None and 300 in duration_windows:
        five_raw = {"remainingPercent": duration_windows[300].remaining_percent, "resetsAt": duration_windows[300].resets_at.isoformat() if duration_windows[300].resets_at else None}
    if weekly_raw is None and 10080 in duration_windows:
        weekly_raw = {"remainingPercent": duration_windows[10080].remaining_percent, "resetsAt": duration_windows[10080].resets_at.isoformat() if duration_windows[10080].resets_at else None}
    codex_warnings = codex_raw.get("dataWarnings", [])
    if isinstance(codex_warnings, list):
        warning_messages.extend(item for item in codex_warnings if isinstance(item, str))
    profile_usage = parse_profile_usage(codex_raw.get("profileUsage"))

    # The Mac/Windows client may have a newer local Codex sample than the
    # NAS-exported feed. Overlay only fresh, duration-classified values so the
    # device shows current quota without exposing credentials or rewriting the
    # shared dashboard file. Account-level app-server totals take precedence
    # over the legacy profile sidecar in the renderer.
    local_usage, local_windows = _read_local_codex_snapshot(
        local_snapshot_path,
        now,
        local_stale_after or timedelta(minutes=15),
    )
    if local_windows:
        if 300 in local_windows:
            item = local_windows[300]
            five_raw = {"remainingPercent": item.remaining_percent, "resetsAt": item.resets_at.isoformat() if item.resets_at else None}
        if 10080 in local_windows:
            item = local_windows[10080]
            weekly_raw = {"remainingPercent": item.remaining_percent, "resetsAt": item.resets_at.isoformat() if item.resets_at else None}
    if local_usage is not None and local_usage.available:
        token_usage = local_usage

    if not token_usage.available and not any("token" in message.lower() for message in warning_messages):
        warning_messages.append("codex_token_usage_unavailable")

    return DashboardFeed(
        schema_version=1,
        generated_at=generated_at,
        quota=QuotaData(
            five_hour=_quota_window(five_raw, "fiveHour"),
            weekly=_quota_window(weekly_raw, "weekly"),
        ),
        repositories=[_repository(item, index) for index, item in enumerate(repositories_raw)],
        warnings=[FeedWarning(message=item) for item in warning_messages],
        token_usage=token_usage,
        contributions=_contributions(contributions_raw.get("lastYear")),
        contribution_total=_nonnegative_int(
            contributions_raw.get("totalContributions"),
            "github.contributions.totalContributions",
        ),
        contribution_status=contribution_status,
        profile_usage=profile_usage,
    )
