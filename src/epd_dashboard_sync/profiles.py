"""Display profiles for the supported EPD panels.

The dimensions and driver ids mirror the controller's documented panel
configuration.  The driver id is kept alongside the dimensions because it is
needed by the BLE INIT command, while rendering itself is driver agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenProfile:
    name: str
    width: int
    height: int
    color_mode: str
    driver_id: str


FOUR_TWO_PROFILE = ScreenProfile(
    name="4.2_400x300_four_color",
    width=400,
    height=300,
    color_mode="four_color",
    driver_id="05",
)

FOUR_TWO_THREE_COLOR_PROFILE = ScreenProfile(
    name="4.2_400x300_three_color",
    width=400,
    height=300,
    color_mode="three_color",
    driver_id="03",
)

THREE_NINETY_EIGHT_PROFILE = ScreenProfile(
    name="3.98_768x552_four_color",
    width=768,
    height=552,
    color_mode="four_color",
    driver_id="13",
)


PROFILES = {
    FOUR_TWO_PROFILE.name: FOUR_TWO_PROFILE,
    FOUR_TWO_THREE_COLOR_PROFILE.name: FOUR_TWO_THREE_COLOR_PROFILE,
    THREE_NINETY_EIGHT_PROFILE.name: THREE_NINETY_EIGHT_PROFILE,
}
