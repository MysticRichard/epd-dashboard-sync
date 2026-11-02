"""Byte-level EPD BLE protocol primitives.

The controller sends one command byte followed by its payload.  Image chunks
are returned without the command byte so callers can choose response pacing at
the transport boundary.
"""

from __future__ import annotations

from typing import Iterable


CMD_SET_PINS = 0x00
CMD_INIT = 0x01
CMD_CLEAR = 0x02
CMD_SEND_CMD = 0x03
CMD_SEND_DATA = 0x04
CMD_REFRESH = 0x05
CMD_SLEEP = 0x06
CMD_WRITE_IMG = 0x30
CMD_SET_SLOT = 0x31
CMD_FREE_SLOT = 0x32
CMD_SET_SLIDE = 0x33
CMD_GET_IMAGE = 0x34
CMD_GET_SLOTS = 0x35

SERVICE_UUID = "62750001-d828-918d-fb46-b6c11c675aec"
CHARACTERISTIC_UUID = "62750002-d828-918d-fb46-b6c11c675aec"
VERSION_UUID = "62750003-d828-918d-fb46-b6c11c675aec"


def encode_command(command: int, payload: bytes = b"") -> bytes:
    if isinstance(command, bool) or not isinstance(command, int) or not 0 <= command <= 0xFF:
        raise ValueError("command must be an 8-bit integer")
    try:
        body = bytes(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be bytes-like") from exc
    return bytes([command]) + body


def _slot_bytes(slot: int, protocol_v2: bool) -> bytes:
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise ValueError("slot must be a non-negative integer")
    if protocol_v2:
        if slot > 0xFFFFFFFF:
            raise ValueError("v2 slot must fit uint32")
        return slot.to_bytes(4, "little")
    # Legacy firmware intentionally consumes only the low byte (the web
    # controller uses ``slot & 0xFF``), including the all-slots sentinel.
    return bytes([slot & 0xFF])


def encode_slot_action(action: int, slot: int, protocol_v2: bool) -> bytes:
    if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action <= 0xFF:
        raise ValueError("action must be an 8-bit integer")
    return bytes([action]) + _slot_bytes(slot, protocol_v2)


def encode_slot_index(slot: int, protocol_v2: bool) -> bytes:
    return _slot_bytes(slot, protocol_v2)


def chunk_image(data: bytes, mtu: int, step: str = "bw") -> Iterable[bytes]:
    """Yield image payloads with the controller's config byte.

    The GATT write includes a command byte in addition to the config byte, so
    at most ``mtu - 3`` image bytes are put in each returned payload.  Bit 1
    marks the first chunk, matching the controller's non-RLE path.
    """
    raw = bytes(data)
    if isinstance(mtu, bool) or not isinstance(mtu, int) or mtu < 4:
        raise ValueError("mtu must be an integer of at least 4")
    if step not in ("bw", "color"):
        raise ValueError("step must be 'bw' or 'color'")
    chunk_size = max(1, mtu - 3)
    color_flag = 0x01 if step == "color" else 0x00
    if not raw:
        yield bytes([color_flag | 0x02])
        return
    for offset in range(0, len(raw), chunk_size):
        config = color_flag | (0x02 if offset == 0 else 0x00)
        yield bytes([config]) + raw[offset : offset + chunk_size]
