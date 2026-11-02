"""Deterministic, host-side rendering for EPD dashboard slots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .feed import DashboardFeed, QuotaWindow, RepositoryStatus
from .profiles import ScreenProfile


Color = Tuple[int, int, int]
PALETTE: Sequence[Color] = (
    (255, 255, 255),  # white
    (0, 0, 0),  # black
    (190, 45, 35),  # red
    (220, 175, 30),  # yellow
)

_FONT_PATHS = (
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_CACHE: Dict[Tuple[int, bool], ImageFont.ImageFont] = {}
_CJK_FONT_CACHE: Dict[Tuple[int, bool], ImageFont.ImageFont] = {}
_GITHUB_ICON_CACHE: Dict[int, Image.Image] = {}
_CODEX_ICON_CACHE: Dict[int, Image.Image] = {}

# The EPD's thresholding makes a one-pixel outline disproportionately heavy on
# large glyphs. Keep the headline weight in the selected bold face, while the
# smaller supporting labels also use a bold face so they survive packing.
COMBINED_MAIN_STROKE_WIDTH = 0
COMBINED_SUPPORTING_TEXT_BOLD = True
COMBINED_CODEX_TITLE = "Codex"
COMBINED_GITHUB_TITLE = "GitHub"
COMBINED_WEEKLY_PERCENT_Y = 158
COMBINED_WEEKLY_LABEL_Y = 193
COMBINED_WEEKLY_BAR_Y = 212


@dataclass(frozen=True)
class RenderedImage:
    width: int
    height: int
    color_mode: str
    packed: bytes
    sha256: str
    png_path: Optional[Path]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = (max(8, int(size)), bool(bold))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = _FONT_PATHS if not bold else (
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/Library/Fonts/Swis721 Hv BT Heavy.ttf",
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                loaded = ImageFont.truetype(candidate, key[0])
                _FONT_CACHE[key] = loaded
                return loaded
            except OSError:
                pass
    loaded = ImageFont.load_default()
    _FONT_CACHE[key] = loaded
    return loaded


def _font_cjk(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a macOS/Windows font with Chinese glyphs for the localized board."""
    key = (max(8, int(size)), bool(bold))
    if key in _CJK_FONT_CACHE:
        return _CJK_FONT_CACHE[key]
    candidates = (
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2 if bold else 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        (("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"), 0),
        (("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
    )
    for candidate, index in candidates:
        if Path(candidate).exists():
            try:
                loaded = ImageFont.truetype(candidate, key[0], index=index)
                _CJK_FONT_CACHE[key] = loaded
                return loaded
            except OSError:
                pass
    loaded = _font(size, bold=bold)
    _CJK_FONT_CACHE[key] = loaded
    return loaded


def _fit_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1] + "…"


def _fit_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    value = text
    while value and draw.textbbox((0, 0), value + "…", font=font)[2] > max_width:
        value = value[:-1]
    return value + "…"


def _scale(profile: ScreenProfile) -> float:
    return min(profile.width / 400.0, profile.height / 300.0)


def _px(value: float, scale: float) -> int:
    return int(round(value * scale))


def _time_text(value: Optional[datetime]) -> str:
    if value is None:
        return "--"
    return value.astimezone().strftime("%m-%d %H:%M")


def _contribution_label(feed: DashboardFeed) -> str:
    """Return a truthful contribution summary for the compact EPD layout.

    GitHub can return an apparently empty calendar when private contribution
    visibility is unavailable.  Never render that state as a verified zero.
    """
    if feed.contribution_status == "private_hidden":
        return "贡献不可见 · 过去一年"
    if feed.contribution_status == "private_unverified":
        return "私有贡献未验证"
    if feed.contribution_status == "unavailable":
        return "贡献数据不可用"
    total = feed.contribution_total if feed.contribution_total is not None else sum(day.count for day in feed.contributions)
    return "%d 次贡献 · 过去一年" % total


def _compact_count(value: int) -> str:
    """Keep token counters legible in the two narrow footer columns."""
    value = int(value)
    if value >= 100_000_000:
        return "%.1f亿" % (value / 100_000_000)
    if value >= 10_000:
        return "%d万" % round(value / 10_000)
    if value >= 1_000:
        return "%.1fk" % (value / 1_000)
    return str(value)


def _draw_compact_token(draw: ImageDraw.ImageDraw, x: int, y: int, value: Optional[int], scale: float) -> None:
    if value is None:
        draw.text((x, y), "--", fill=PALETTE[1], font=_font_cjk(_px(19, scale), bold=True))
        return
    text = _compact_count(value)
    suffix = ""
    number = text
    if text.endswith("亿"):
        number, suffix = text[:-1], "亿"
    elif text.endswith("万"):
        number, suffix = text[:-1], "万"
    number_font = _font_cjk(_px(25, scale), bold=True)
    suffix_font = _font_cjk(_px(11, scale), bold=True)
    draw.text((x, y), number, fill=PALETTE[1], font=number_font, stroke_width=0)
    if suffix:
        number_width = draw.textbbox((x, y), number, font=number_font)[2] - x
        draw.text((x + number_width + _px(2, scale), y + _px(8, scale)), suffix, fill=PALETTE[1], font=suffix_font, stroke_width=0)


def _draw_codex_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    asset = Path(__file__).with_name("assets") / "codex-mark.png"
    if asset.exists():
        icon = _CODEX_ICON_CACHE.get(size)
        if icon is None:
            try:
                icon = Image.open(asset).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
                _CODEX_ICON_CACHE[size] = icon
            except OSError:
                icon = None
        if icon is not None:
            draw.bitmap((x, y), icon.getchannel("A"), fill=PALETTE[1])
            return
    cx, cy = x + size // 2, y + size // 2
    radius = max(3, size // 3)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=PALETTE[1], width=max(1, size // 10))
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        draw.line((cx, cy, cx + dx * size // 2, cy + dy * size // 2), fill=PALETTE[2], width=max(1, size // 8))
    dot = max(2, size // 10)
    draw.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=PALETTE[2])


def _draw_github_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    # Use the supplied official Octicon path when the packaged raster asset is
    # available. Its alpha channel keeps the icon edge clean on white EPD paper.
    asset = Path(__file__).with_name("assets") / "github-mark.png"
    if asset.exists():
        icon = _GITHUB_ICON_CACHE.get(size)
        if icon is None:
            try:
                icon = Image.open(asset).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
                _GITHUB_ICON_CACHE[size] = icon
            except OSError:
                icon = None
        if icon is not None:
            draw.bitmap((x, y), icon.getchannel("A"), fill=PALETTE[1])
            return
    black, white = PALETTE[1], PALETTE[0]
    draw.polygon(((x + size // 5, y + size // 3), (x + size // 5, y), (x + size // 2, y + size // 5)), fill=black)
    draw.polygon(((x + size * 4 // 5, y + size // 3), (x + size * 4 // 5, y), (x + size // 2, y + size // 5)), fill=black)
    draw.ellipse((x + size // 5, y + size // 5, x + size * 4 // 5, y + size * 4 // 5), fill=black)
    eye = max(1, size // 12)
    draw.ellipse((x + size * 3 // 10, y + size * 2 // 5, x + size * 3 // 10 + eye, y + size * 2 // 5 + eye), fill=white)
    draw.ellipse((x + size * 13 // 20, y + size * 2 // 5, x + size * 13 // 20 + eye, y + size * 2 // 5 + eye), fill=white)


def _draw_lock(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    width = max(1, size // 8)
    draw.rectangle((x, y + size // 3, x + size, y + size), outline=PALETTE[1], width=width)
    draw.arc((x + size // 5, y, x + size * 4 // 5, y + size * 3 // 5), 180, 360, fill=PALETTE[1], width=width)


def _draw_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str, profile: ScreenProfile, icon: str) -> None:
    scale = _scale(profile)
    size = _px(25, scale)
    x, y = _px(12, scale), _px(8, scale)
    (_draw_codex_icon if icon == "codex" else _draw_github_icon)(draw, x, y, size)
    draw.text((_px(45, scale), _px(7, scale)), title, fill=PALETTE[1], font=_font(_px(20, scale), bold=True))
    draw.text((_px(45, scale), _px(31, scale)), subtitle, fill=PALETTE[1], font=_font(_px(11, scale)))


def _draw_quota(draw: ImageDraw.ImageDraw, label: str, window: Optional[QuotaWindow], x: int, y: int, width: int, scale: float) -> None:
    height = _px(75, scale)
    draw.rectangle((x, y, x + width, y + height), outline=PALETTE[1], width=max(1, _px(1, scale)))
    inner_x = x + _px(11, scale)
    label_font = _font(_px(13, scale), bold=True)
    percent_font = _font(_px(24, scale), bold=True)
    if window is None:
        draw.text((inner_x, y + _px(7, scale)), label.upper(), fill=PALETTE[1], font=label_font)
        draw.text((x + width - _px(48, scale), y + _px(3, scale)), "--", fill=PALETTE[1], font=percent_font)
        return
    percent = max(0.0, min(100.0, window.remaining_percent))
    percent_text = "%.0f%%" % percent
    percent_box = draw.textbbox((0, 0), percent_text, font=percent_font)
    draw.text((inner_x, y + _px(7, scale)), label.upper(), fill=PALETTE[1], font=label_font)
    draw.text((x + width - (percent_box[2] - percent_box[0]) - _px(12, scale), y + _px(1, scale)), percent_text, fill=PALETTE[2], font=percent_font)
    bar_y = y + _px(36, scale)
    bar_height = _px(13, scale)
    bar_width = width - _px(24, scale)
    _draw_rounded_bar(draw, inner_x, bar_y, bar_width, bar_height, percent, scale)
    draw.text((inner_x, y + _px(55, scale)), "reset " + _time_text(window.resets_at), fill=PALETTE[1], font=_font(_px(11, scale)))


def _draw_rounded_bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, percent: float, scale: float) -> None:
    """Draw a pill-shaped red progress bar that survives 1-bit packing."""
    radius = max(2, height // 2)
    outline = max(1, _px(1, scale))
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, outline=PALETTE[1], width=outline)
    fill_width = int(round(max(0, width - outline * 2) * max(0.0, min(100.0, percent)) / 100.0))
    if fill_width > 0:
        draw.rounded_rectangle(
            (x + outline, y + outline, x + outline + fill_width, y + height - outline),
            radius=max(1, radius - outline),
            fill=PALETTE[2],
        )


def _render_codex(feed: DashboardFeed, profile: ScreenProfile) -> Image.Image:
    image = Image.new("RGB", (profile.width, profile.height), PALETTE[0])
    draw = ImageDraw.Draw(image)
    scale = _scale(profile)
    _draw_header(draw, "CODEX QUOTA", "updated " + _time_text(feed.generated_at), profile, "codex")
    x, width = _px(12, scale), _px(376, scale)
    _draw_quota(draw, "5-hour", feed.quota.five_hour, x, _px(54, scale), width, scale)
    _draw_quota(draw, "weekly", feed.quota.weekly, x, _px(136, scale), width, scale)
    y = _px(224, scale)
    draw.text((x, y), "WARNINGS", fill=PALETTE[1], font=_font(_px(12, scale), bold=True))
    if feed.warnings:
        for warning in feed.warnings[:4]:
            y += _px(16, scale)
            font = _font(_px(11, scale))
            draw.text((_px(18, scale), y), _fit_text_width(draw, "! " + warning.message, font, profile.width - _px(30, scale)), fill=PALETTE[2], font=font)
    else:
        draw.text((_px(18, scale), y + _px(16, scale)), "ALL CLEAR", fill=PALETTE[2], font=_font(_px(11, scale), bold=True))
    return image


def _workflow_text(repo: RepositoryStatus) -> str:
    workflow = repo.workflow
    if workflow.conclusion:
        return workflow.conclusion
    if workflow.status:
        return workflow.status
    return "unknown"


def _render_github(feed: DashboardFeed, profile: ScreenProfile) -> Image.Image:
    image = Image.new("RGB", (profile.width, profile.height), PALETTE[0])
    draw = ImageDraw.Draw(image)
    scale = _scale(profile)
    _draw_header(draw, "GITHUB PROJECTS", "%d repositories · updated %s" % (len(feed.repositories), _time_text(feed.generated_at)), profile, "github")
    left, right = _px(12, scale), profile.width - _px(12, scale)
    y, row_height = _px(54, scale), _px(57, scale)
    name_font, detail_font = _font(_px(15, scale), bold=True), _font(_px(11, scale))
    for repo in feed.repositories[:4]:
        draw.line((left, y + row_height - _px(2, scale), right, y + row_height - _px(2, scale)), fill=PALETTE[1], width=max(1, _px(1, scale)))
        title = _fit_text_width(draw, repo.full_name, name_font, profile.width - _px(55, scale))
        draw.text((left, y + _px(3, scale)), title, fill=PALETTE[1], font=name_font)
        if repo.private:
            _draw_lock(draw, right - _px(15, scale), y + _px(6, scale), _px(10, scale))
        summary = "ISS:%s   PR:%s   %s" % (
            "--" if repo.open_issues is None else repo.open_issues,
            "--" if repo.open_pull_requests is None else repo.open_pull_requests,
            "PRIVATE" if repo.private else "PUBLIC",
        )
        draw.text((left, y + _px(24, scale)), summary, fill=PALETTE[1], font=detail_font)
        workflow = _workflow_text(repo).upper()
        conclusion_color = PALETTE[2] if _workflow_text(repo) in ("failure", "cancelled", "timed_out") else PALETTE[1]
        draw.text((left, y + _px(40, scale)), "WORKFLOW: " + _fit_text_width(draw, workflow, detail_font, profile.width - _px(25, scale)), fill=conclusion_color, font=detail_font)
        y += row_height
        if y >= profile.height - _px(15, scale):
            break
    return image


def _render_combined(feed: DashboardFeed, profile: ScreenProfile) -> Image.Image:
    """Render a single 400x300 board with localized Codex and GitHub activity."""
    image = Image.new("RGB", (profile.width, profile.height), PALETTE[0])
    draw = ImageDraw.Draw(image)
    scale = _scale(profile)
    black, red = PALETTE[1], PALETTE[2]

    # No dashboard title rail: use the full height for readable metrics on the
    # physical 400x300 panel.
    split_x = _px(200, scale)
    draw.line((split_x, _px(7, scale), split_x, profile.height - _px(8, scale)), fill=black, width=max(1, _px(1, scale)))

    # Codex: Chinese labels, larger type and pill-shaped bars.
    left = _px(13, scale)
    cjk = _font_cjk(_px(10, scale), bold=COMBINED_SUPPORTING_TEXT_BOLD)
    cjk_small = _font_cjk(_px(12, scale), bold=COMBINED_SUPPORTING_TEXT_BOLD)
    section_icon = _px(27, scale)
    _draw_codex_icon(draw, left, _px(8, scale), section_icon)
    draw.text((left + _px(33, scale), _px(9, scale)), COMBINED_CODEX_TITLE, fill=black, font=_font(_px(20, scale), bold=True), stroke_width=COMBINED_MAIN_STROKE_WIDTH)
    five = feed.quota.five_hour
    five_percent = max(0.0, min(100.0, five.remaining_percent)) if five is not None else None
    main_text = "--" if five_percent is None else "%.0f%%" % five_percent
    draw.text((left, _px(39, scale)), main_text, fill=red if five_percent is not None else black, font=_font(_px(38, scale), bold=True), stroke_width=COMBINED_MAIN_STROKE_WIDTH)
    draw.text((left, _px(91, scale)), "5 小时剩余", fill=black, font=_font_cjk(_px(14, scale), bold=True))
    bar_x, bar_y, bar_w, bar_h = left, _px(110, scale), split_x - left - _px(19, scale), _px(16, scale)
    _draw_rounded_bar(draw, bar_x, bar_y, bar_w, bar_h, five_percent or 0.0, scale)
    if five is not None:
        draw.text((left, _px(132, scale)), "重置 " + _time_text(five.resets_at), fill=black, font=cjk_small)

    weekly = feed.quota.weekly
    weekly_percent = max(0.0, min(100.0, weekly.remaining_percent)) if weekly is not None else None
    weekly_text = "--" if weekly_percent is None else "%.0f%%" % weekly_percent
    draw.text((left, _px(COMBINED_WEEKLY_PERCENT_Y, scale)), weekly_text, fill=red if weekly_percent is not None else black, font=_font(_px(29, scale), bold=True), stroke_width=COMBINED_MAIN_STROKE_WIDTH)
    draw.text((left, _px(COMBINED_WEEKLY_LABEL_Y, scale)), "每周剩余", fill=black, font=_font_cjk(_px(14, scale), bold=True))
    bar_y = _px(COMBINED_WEEKLY_BAR_Y, scale)
    _draw_rounded_bar(draw, bar_x, bar_y, bar_w, _px(13, scale), weekly_percent or 0.0, scale)
    if weekly is not None:
        draw.text((left, _px(228, scale)), "重置 " + _time_text(weekly.resets_at), fill=black, font=cjk_small)
    # Token totals follow the two-column treatment from the reference image.
    token_y = _px(252, scale)
    separator_x = _px(105, scale)
    token_label_font = _font_cjk(_px(12, scale), bold=True)
    draw.text((left, token_y), "累计 Token", fill=black, font=token_label_font)
    draw.text((separator_x + _px(9, scale), token_y), "近七日 Token", fill=black, font=token_label_font)
    draw.line((separator_x, token_y - _px(2, scale), separator_x, token_y + _px(31, scale)), fill=black, width=max(1, _px(1, scale)))
    # The app-server snapshot is account-level; the sidecar is a legacy
    # fallback. Local session counters remain below both when no sidecar exists.
    account_snapshot = feed.token_usage.source == "codex-app-server-account-usage"
    total_tokens = feed.token_usage.total if account_snapshot and feed.token_usage.total is not None else feed.profile_usage.total
    if total_tokens is None:
        total_tokens = feed.token_usage.total
    last_7_days_tokens = feed.token_usage.last_7_days if account_snapshot else feed.profile_usage.last_7_days
    if last_7_days_tokens is None:
        last_7_days_tokens = feed.token_usage.last_7_days
    _draw_compact_token(draw, left, token_y + _px(16, scale), total_tokens, scale)
    _draw_compact_token(draw, separator_x + _px(9, scale), token_y + _px(16, scale), last_7_days_tokens, scale)

    # GitHub: screenshot-inspired contribution heatmap plus activity rows.
    right_left = split_x + _px(10, scale)
    right_right = profile.width - _px(9, scale)
    right_width = right_right - right_left
    _draw_github_icon(draw, right_left, _px(8, scale), section_icon)
    draw.text((right_left + _px(33, scale), _px(9, scale)), COMBINED_GITHUB_TITLE, fill=black, font=_font(_px(19, scale), bold=True), stroke_width=COMBINED_MAIN_STROKE_WIDTH)
    contribution_days = list(feed.contributions[-105:])
    contribution_counts = [day.count for day in contribution_days]
    if contribution_counts:
        max_contribution = max(contribution_counts)
    else:
        max_contribution = 0
    draw.text((right_left, _px(43, scale)), _contribution_label(feed), fill=black, font=_font_cjk(_px(11, scale), bold=COMBINED_SUPPORTING_TEXT_BOLD))
    # 15×7 GitHub contribution calendar, populated from the sanitized source.
    gap = _px(2, scale)
    columns = 15
    cell_w = max(_px(5, scale), int((right_width - gap * (columns - 1)) / columns))
    cell_h = _px(7, scale)
    heat_x, heat_y = right_left, _px(60, scale)
    for col in range(columns):
        for row in range(7):
            index = col * 7 + row
            count = contribution_counts[index] if index < len(contribution_counts) else 0
            fill = black if count > 0 else PALETTE[0]
            x0 = heat_x + col * (cell_w + gap)
            y0 = heat_y + row * (cell_h + gap)
            draw.rounded_rectangle((x0, y0, x0 + cell_w, y0 + cell_h), radius=max(1, cell_h // 3), fill=fill, outline=black, width=1)
    # Leave a clear breathing space between the calendar and the activity list.
    draw.text((right_left, _px(142, scale)), "贡献活动", fill=black, font=_font_cjk(_px(14, scale), bold=True))
    commit_counts = [repo.commit_count for repo in feed.repositories[:3] if repo.commit_count is not None]
    max_commits = max(commit_counts or [1])
    row_y = _px(165, scale)
    row_h = _px(39, scale)
    name_font, detail_font = _font(_px(11, scale), bold=True), _font(_px(10, scale), bold=True)
    if not feed.repositories:
        draw.text((right_left, _px(176, scale)), "未配置 GitHub 数据", fill=black, font=_font_cjk(_px(12, scale), bold=True))
    for index, repo in enumerate(feed.repositories[:3]):
        count = repo.commit_count
        count_text = "-- commits" if count is None else "%d commits" % count
        bar_w = max(_px(5, scale), int((right_width - _px(76, scale)) * count / max_commits)) if count is not None else 0
        bar_x = right_right - bar_w
        title = _fit_text_width(draw, repo.full_name.rsplit("/", 1)[-1], name_font, right_width)
        draw.text((right_left, row_y), title, fill=black, font=name_font)
        # Keep commits and the thin progress bar on the second line, matching
        # GitHub's activity list while leaving the project name unobstructed.
        detail_y = row_y + _px(15, scale)
        draw.text((right_left, detail_y), count_text, fill=black, font=detail_font)
        if bar_w:
            draw.rounded_rectangle((bar_x, detail_y + _px(3, scale), right_right, detail_y + _px(9, scale)), radius=max(1, _px(2, scale)), fill=red if index == 0 else black)
        row_y += row_h

    return image


def _palette_index(pixel: Color) -> int:
    red, green, blue = pixel
    # Preserve the two accent colors before falling back to luminance. This
    # produces a stable 2-bit representation for the EPD's four-color mode.
    if red > 130 and green > 110 and blue < 100:
        return 3
    if red > 120 and green < 100 and blue < 100:
        return 2
    luminance = (299 * red + 587 * green + 114 * blue) // 1000
    return 1 if luminance < 128 else 0


def pack_2bpp(image: Image.Image) -> bytes:
    """Pack four color indices per byte, leftmost pixel in the high bits."""
    rgb = image.convert("RGB")
    pixels = rgb.load()
    values = []
    for y in range(rgb.height):
        for x in range(rgb.width):
            values.append(_palette_index(pixels[x, y]))
    packed = bytearray((len(values) + 3) // 4)
    for index, value in enumerate(values):
        packed[index // 4] |= (value & 0x03) << (6 - (index % 4) * 2)
    return bytes(packed)


def pack_three_color(image: Image.Image) -> bytes:
    """Pack black and red 1bpp planes in the controller's three-color order."""
    rgb = image.convert("RGB")
    bytes_per_row = (rgb.width + 7) // 8
    plane_size = bytes_per_row * rgb.height
    black = bytearray([0xFF] * plane_size)
    red = bytearray([0xFF] * plane_size)
    pixels = rgb.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = pixels[x, y]
            index = y * bytes_per_row + x // 8
            bit = 0x80 >> (x & 7)
            if r > 120 and g < 120 and b < 120:
                red[index] &= ~bit
            else:
                luminance = (299 * r + 587 * g + 114 * b) // 1000
                if luminance < 128:
                    black[index] &= ~bit
    return bytes(black + red)


def pack_image(image: Image.Image, color_mode: str) -> bytes:
    if color_mode == "three_color":
        return pack_three_color(image)
    if color_mode == "four_color":
        return pack_2bpp(image)
    raise ValueError("unsupported color mode: %s" % color_mode)


def _render_one(kind: str, feed: DashboardFeed, profile: ScreenProfile) -> Image.Image:
    if kind == "codex":
        return _render_codex(feed, profile)
    if kind == "github":
        return _render_github(feed, profile)
    raise ValueError("unknown dashboard kind: %s" % kind)


def render_dashboards(feed: DashboardFeed, profile: ScreenProfile, output_dir: Optional[Path] = None) -> Dict[str, RenderedImage]:
    """Render the Codex and GitHub slots and return packed bytes plus hashes."""
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for kind in ("codex", "github"):
        image = _render_one(kind, feed, profile)
        packed = pack_image(image, profile.color_mode)
        digest = hashlib.sha256(packed).hexdigest()
        png_path = None
        if output_dir is not None:
            png_path = output_dir / (kind + ".png")
            image.save(str(png_path), format="PNG", optimize=False)
        rendered[kind] = RenderedImage(profile.width, profile.height, profile.color_mode, packed, digest, png_path)
    return rendered


def render_combined_dashboard(feed: DashboardFeed, profile: ScreenProfile, output_dir: Optional[Path] = None) -> RenderedImage:
    """Render Codex and GitHub content together as one board image."""
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    image = _render_combined(feed, profile)
    packed = pack_image(image, profile.color_mode)
    digest = hashlib.sha256(packed).hexdigest()
    png_path = None
    if output_dir is not None:
        png_path = output_dir / "combined.png"
        image.save(str(png_path), format="PNG", optimize=False)
    return RenderedImage(profile.width, profile.height, profile.color_mode, packed, digest, png_path)
