from __future__ import annotations

from typing import Callable, List, Optional, Tuple


class FakeTransport:
    """In-memory transport used to prove protocol code without BLE hardware."""

    def __init__(self, version: bytes = b"") -> None:
        self.version = version
        self.writes: List[Tuple[bytes, bool]] = []
        self.destructive_writes: List[bytes] = []
        self.connected = False
        self._callback: Optional[Callable[[bytes], None]] = None

    def scan(self, timeout: float = 8.0):
        return []

    def connect(self, identifier: str) -> None:
        self.connected = True

    def read_version(self) -> bytes:
        return self.version

    def start_notifications(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback

    def write(self, payload: bytes, response: bool = True) -> None:
        self.writes.append((bytes(payload), response))

    def disconnect(self) -> None:
        self.connected = False
