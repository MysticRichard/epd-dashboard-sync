from dataclasses import replace
from datetime import datetime
from pathlib import Path

from epd_dashboard_sync.feed import (
    DashboardFeed,
    ContributionDay,
    TokenUsage,
    TokenWindow,
    QuotaData,
    QuotaWindow,
    RepositoryStatus,
    WorkflowStatus,
)
from epd_dashboard_sync.profile import ProfileTokenUsage
from epd_dashboard_sync.profiles import FOUR_TWO_PROFILE, FOUR_TWO_THREE_COLOR_PROFILE, THREE_NINETY_EIGHT_PROFILE
from epd_dashboard_sync import render as render_module
from epd_dashboard_sync.render import _contribution_label, _font, _font_cjk, render_combined_dashboard, render_dashboards


NOW = datetime.fromisoformat("2026-08-30T10:00:00+08:00")


def make_feed() -> DashboardFeed:
    return DashboardFeed(
        schema_version=1,
        generated_at=NOW,
        quota=QuotaData(
            five_hour=QuotaWindow(68.0, datetime.fromisoformat("2026-08-30T12:14:00+08:00")),
            weekly=QuotaWindow(42.0, datetime.fromisoformat("2026-09-03T08:00:00+08:00")),
        ),
        repositories=[
            RepositoryStatus(
                full_name="example-org/example-project",
                private=True,
                default_branch="main",
                open_issues=3,
                open_pull_requests=1,
                latest_commit_at=NOW,
                workflow=WorkflowStatus("completed", "success", NOW),
            )
        ],
        warnings=[],
    )


def test_render_4_2_dashboard_has_native_dimensions_and_stable_hash(tmp_path: Path) -> None:
    feed = make_feed()
    first = render_dashboards(feed, FOUR_TWO_PROFILE, tmp_path)["codex"]
    second = render_dashboards(feed, FOUR_TWO_PROFILE, tmp_path)["codex"]

    assert (first.width, first.height) == (400, 300)
    assert len(first.packed) == 400 * 300 // 4
    assert first.sha256 == second.sha256
    assert (tmp_path / "codex.png").exists()


def test_dashboard_font_scales_for_readable_headings() -> None:
    font = _font(24)
    assert font.getbbox("CODEX QUOTA")[2] >= 100


def test_cjk_bold_font_uses_heavier_hiragino_face_when_available() -> None:
    regular = _font_cjk(18, bold=False)
    bold = _font_cjk(18, bold=True)
    if regular.getname()[0] != "Hiragino Sans GB":
        return
    assert regular.getname()[1] == "W3"
    assert bold.getname()[1] == "W6"


def test_combined_dashboard_has_native_dimensions_and_packed_planes(tmp_path: Path) -> None:
    result = render_combined_dashboard(make_feed(), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert (result.width, result.height) == (400, 300)
    assert len(result.packed) == 2 * ((400 + 7) // 8) * 300
    assert (tmp_path / "combined.png").exists()


def test_render_3_98_dashboard_has_native_dimensions(tmp_path: Path) -> None:
    result = render_dashboards(make_feed(), THREE_NINETY_EIGHT_PROFILE, tmp_path)["github"]

    assert (result.width, result.height) == (768, 552)
    assert len(result.packed) == 768 * 552 // 4
    assert (tmp_path / "github.png").exists()


def test_render_4_2_three_color_uses_two_one_bpp_planes(tmp_path: Path) -> None:
    result = render_dashboards(make_feed(), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)["codex"]
    assert (result.width, result.height) == (400, 300)
    assert len(result.packed) == 2 * ((400 + 7) // 8) * 300


def test_combined_dashboard_hash_changes_with_live_token_and_github_data(tmp_path: Path) -> None:
    base = make_feed()
    live = replace(
        base,
        token_usage=TokenUsage(True, TokenWindow(1200, 5000), TokenWindow(3400, 10000)),
        contributions=[ContributionDay("2026-08-29", 7)],
        repositories=[replace(base.repositories[0], commit_count=34)],
    )
    static_hash = render_combined_dashboard(base, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "static").sha256
    live_hash = render_combined_dashboard(live, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "live").sha256

    assert static_hash != live_hash


def test_combined_dashboard_shows_github_empty_state_in_activity_area(tmp_path: Path) -> None:
    result = render_combined_dashboard(replace(make_feed(), repositories=[]), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    image = result.png_path
    assert image is not None
    from PIL import Image

    rendered = Image.open(image).convert("RGB")
    dark_pixels = sum(
        1
        for pixel in rendered.crop((220, 180, 399, 295)).getdata()
        if sum(pixel) < 180
    )
    assert dark_pixels > 20


def test_combined_dashboard_keeps_weekly_percent_close_to_label(tmp_path: Path) -> None:
    result = render_combined_dashboard(make_feed(), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert result.png_path is not None
    from PIL import Image

    rendered = Image.open(result.png_path).convert("RGB")
    red_y = [
        y
        for y in range(150, 204)
        for x in range(0, 190)
        if (lambda pixel: pixel[0] > 120 and pixel[1] < 100 and pixel[2] < 100)(rendered.getpixel((x, y)))
    ]
    assert min(red_y) <= 175


def test_combined_dashboard_places_token_values_low_in_footer(tmp_path: Path) -> None:
    feed = replace(make_feed(), token_usage=TokenUsage(True, None, None, total=123_456_789, peak_daily=123_456_789, last_7_days=123_456_789))
    result = render_combined_dashboard(feed, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert result.png_path is not None
    from PIL import Image

    rendered = Image.open(result.png_path).convert("RGB")
    dark_y = [
        y
        for y in range(265, 300)
        for x in range(0, 195)
        if sum(rendered.getpixel((x, y))) < 180
    ]
    assert max(dark_y) >= 280


def test_combined_dashboard_uses_profile_totals_over_local_counter(tmp_path: Path) -> None:
    profile = ProfileTokenUsage(23_456_789, 34_567_890, "2026-08-30T12:00:00+08:00")
    first = replace(
        make_feed(),
        token_usage=TokenUsage(True, None, None, total=45_678_901, last_7_days=45_678_901),
        profile_usage=profile,
    )
    second = replace(
        first,
        token_usage=TokenUsage(True, None, None, total=999, last_7_days=888),
    )
    assert render_combined_dashboard(first, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "first").sha256 == render_combined_dashboard(
        second, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "second"
    ).sha256


def test_combined_dashboard_uses_account_snapshot_over_legacy_profile_sidecar(tmp_path: Path) -> None:
    profile = ProfileTokenUsage(23_456_789, 34_567_890, "2026-08-30T12:00:00+08:00")
    account = TokenUsage(
        True,
        None,
        None,
        total=123_456_789,
        last_7_days=23_456_789,
        source="codex-app-server-account-usage",
    )
    first = replace(make_feed(), token_usage=account, profile_usage=profile)
    second = replace(first, profile_usage=ProfileTokenUsage(9, 8, "2026-08-30T12:00:00+08:00"))

    assert render_combined_dashboard(first, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "first").sha256 == render_combined_dashboard(
        second, FOUR_TWO_THREE_COLOR_PROFILE, tmp_path / "second"
    ).sha256


def test_combined_dashboard_token_number_is_large_enough_for_epd(tmp_path: Path) -> None:
    profile = ProfileTokenUsage(23_456_789, 34_567_890, "2026-08-30T12:00:00+08:00")
    result = render_combined_dashboard(replace(make_feed(), profile_usage=profile), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert result.png_path is not None
    from PIL import Image

    rendered = Image.open(result.png_path).convert("RGB")
    dark = [
        (x, y)
        for y in range(268, 299)
        for x in range(0, 195)
        if sum(rendered.getpixel((x, y))) < 180
    ]
    assert dark
    assert max(y for _, y in dark) - min(y for _, y in dark) >= 21


def test_compact_count_uses_chinese_account_units() -> None:
    assert render_module._compact_count(1_234_567_890) == "12.3亿"
    assert render_module._compact_count(234_567_890) == "2.3亿"


def test_combined_dashboard_uses_account_total_and_last_seven_days(monkeypatch, tmp_path: Path) -> None:
    values = []
    monkeypatch.setattr(render_module, "_draw_compact_token", lambda draw, x, y, value, scale: values.append(value))
    usage = TokenUsage(
        True,
        None,
        None,
        total=1_234_567_890,
        last_7_days=234_567_890,
        peak_daily=45_678_901,
        source="codex-app-server-account-usage",
    )
    result = render_combined_dashboard(replace(make_feed(), token_usage=usage), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert result.png_path is not None
    assert values == [1_234_567_890, 234_567_890]


def test_combined_dashboard_uses_local_session_totals(monkeypatch, tmp_path: Path) -> None:
    values = []
    monkeypatch.setattr(render_module, "_draw_compact_token", lambda draw, x, y, value, scale: values.append(value))
    usage = TokenUsage(
        True,
        None,
        None,
        total=345_678_901,
        last_7_days=56_789_012,
        source="local-session-log",
    )
    result = render_combined_dashboard(replace(make_feed(), token_usage=usage), FOUR_TWO_THREE_COLOR_PROFILE, tmp_path)
    assert result.png_path is not None
    assert values == [345_678_901, 56_789_012]


def test_combined_dashboard_balances_main_and_supporting_text_weight() -> None:
    assert render_module.COMBINED_MAIN_STROKE_WIDTH == 0
    assert render_module.COMBINED_SUPPORTING_TEXT_BOLD is True


def test_combined_dashboard_places_weekly_label_below_percent() -> None:
    assert render_module.COMBINED_WEEKLY_PERCENT_Y < render_module.COMBINED_WEEKLY_LABEL_Y
    assert render_module.COMBINED_WEEKLY_LABEL_Y < render_module.COMBINED_WEEKLY_BAR_Y


def test_combined_dashboard_uses_title_case_product_names() -> None:
    assert render_module.COMBINED_CODEX_TITLE == "Codex"
    assert render_module.COMBINED_GITHUB_TITLE == "GitHub"


def test_contribution_label_does_not_claim_zero_when_private_contributions_are_hidden() -> None:
    feed = replace(make_feed(), contribution_status="private_hidden", contributions=[])

    assert _contribution_label(feed) == "贡献不可见 · 过去一年"


def test_contribution_label_explains_private_visibility_is_unverified() -> None:
    feed = replace(make_feed(), contribution_status="private_unverified", contributions=[])

    assert _contribution_label(feed) == "私有贡献未验证"


def test_contribution_label_uses_provider_total_over_visible_days() -> None:
    feed = replace(
        make_feed(),
        contributions=[ContributionDay("2026-08-29", 7)],
        contribution_total=120,
        contribution_status="ok",
    )

    assert _contribution_label(feed) == "120 次贡献 · 过去一年"
