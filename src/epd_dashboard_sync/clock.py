"""Clock synchronization for the optional DA14585 RXTX clock panel."""

from __future__ import annotations

import asyncio
from datetime import datetime
import inspect
import time
from typing import Any, Callable, Optional


CLOCK_CHARACTERISTIC_UUID = "00001f1f-0000-1000-8000-00805f9b34fb"
CLOCK_MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
SUPPORTED_MODEL = "DA14585"
SET_TIME_COMMAND = 0xDD
COMMIT_COMMAND = 0xE2


def encode_set_time(timestamp: int, *, year: int, month: int, day: int, weekday: int) -> bytes:
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 <= timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp must fit uint32")
    if not 0 <= year <= 0xFFFF or not 1 <= month <= 12 or not 1 <= day <= 31 or not 1 <= weekday <= 7:
        raise ValueError("calendar fields are out of range")
    return (
        bytes([SET_TIME_COMMAND])
        + timestamp.to_bytes(4, "big")
        + year.to_bytes(2, "big")
        + bytes([month, day, weekday])
    )


def sync_clock(
    identifier: str,
    now: datetime,
    client_factory: Optional[Callable[..., Any]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Set the clock panel time with its narrowly scoped RXTX command."""
    if client_factory is None:
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - depends on host setup
            raise RuntimeError("Bleak 未安装；时钟屏同步需要安装 bleak") from exc
        client_factory = BleakClient
    asyncio.run(_sync_clock(client_factory, identifier, now, sleep))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _sync_clock(
    client_factory: Callable[..., Any],
    identifier: str,
    now: datetime,
    sleep: Callable[[float], Any],
) -> None:
    payload = encode_set_time(
        int(now.timestamp()),
        year=now.year,
        month=now.month,
        day=now.day,
        weekday=now.isoweekday(),
    )
    client = client_factory(identifier, timeout=20.0)
    try:
        for attempt in range(3):
            try:
                await _maybe_await(client.connect())
                break
            except Exception:
                if attempt == 2:
                    raise
                await _maybe_await(sleep(2.0))
        model = bytes(await _maybe_await(client.read_gatt_char(CLOCK_MODEL_UUID))).decode("ascii", "replace").strip()
        if model != SUPPORTED_MODEL:
            raise RuntimeError("时钟屏型号不受支持：%s" % (model or "unknown"))
        await _maybe_await(client.write_gatt_char(CLOCK_CHARACTERISTIC_UUID, payload, response=True))
        await _maybe_await(client.write_gatt_char(CLOCK_CHARACTERISTIC_UUID, bytes([COMMIT_COMMAND]), response=True))
    finally:
        try:
            await _maybe_await(client.disconnect())
        except Exception:
            pass
