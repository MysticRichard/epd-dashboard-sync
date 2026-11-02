"""Transport boundary for fake tests and optional Bleak-backed macOS BLE."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable, Optional

from .protocol import CHARACTERISTIC_UUID, SERVICE_UUID, VERSION_UUID


class BleTransport:
    """Small synchronous facade over Bleak, imported only when used.

    Keeping the import lazy lets feed/render/protocol tests run on machines
    without CoreBluetooth or the Bleak dependency installed.
    """

    CONNECT_TIMEOUT_SECONDS = 20.0
    WRITE_TIMEOUT_SECONDS = 10.0

    def __init__(self, service_uuid: str = SERVICE_UUID, characteristic_uuid: str = CHARACTERISTIC_UUID, version_uuid: str = VERSION_UUID) -> None:
        self.service_uuid = service_uuid
        self.characteristic_uuid = characteristic_uuid
        self.version_uuid = version_uuid
        self._client: Any = None
        self._version: bytes = b""
        self._notifications_started = False
        self._notification_callback: Optional[Callable[[bytes], None]] = None
        self._notification_queue: Optional[asyncio.Queue[bytes]] = None
        # Bleak binds a client and its CoreBluetooth futures to the event loop
        # used during connect. Keep one loop for the entire synchronous session.
        self._loop = asyncio.new_event_loop()

    @staticmethod
    def _bleak() -> Any:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:  # pragma: no cover - depends on host setup
            raise RuntimeError("Bleak 未安装；设备同步需要安装 bleak") from exc
        return BleakClient, BleakScanner

    def _run(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop.is_closed():
                raise RuntimeError("BLE 事件循环已关闭")
            return self._loop.run_until_complete(coro)
        if hasattr(coro, "close"):
            coro.close()
        raise RuntimeError("BleTransport 不能在已有事件循环中同步调用")

    def _run_with_timeout(self, coro: Any, timeout: float, operation: str) -> Any:
        try:
            return self._run(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError as exc:
            raise RuntimeError("BLE %s超时（%.0f秒）" % (operation, timeout)) from exc

    def scan(self, timeout: float = 8.0) -> Iterable[Any]:
        _, scanner = self._bleak()
        try:
            return self._run(scanner.discover(timeout=timeout, service_uuids=[self.service_uuid]))
        except Exception as exc:  # pragma: no cover - host Bluetooth state
            raise RuntimeError("BLE 扫描失败：%s" % exc) from exc

    def connect(self, identifier: str) -> None:
        client_cls, _ = self._bleak()
        self._client = client_cls(identifier, timeout=self.CONNECT_TIMEOUT_SECONDS)
        try:
            self._run_with_timeout(self._client.connect(), self.CONNECT_TIMEOUT_SECONDS, "连接")
        except Exception as exc:  # pragma: no cover - host Bluetooth state
            raise RuntimeError("BLE 连接失败：%s" % exc) from exc

    def read_version(self) -> bytes:
        if self._client is None:
            raise RuntimeError("尚未连接设备")
        self._version = bytes(self._run(self._client.read_gatt_char(self.version_uuid)))
        return self._version

    def start_notifications(self, callback: Callable[[bytes], None]) -> None:
        if self._client is None:
            raise RuntimeError("尚未连接设备")
        self._notification_callback = callback
        if self._notification_queue is None:
            self._notification_queue = asyncio.Queue()
        if self._notifications_started:
            return

        def handler(_: Any, data: bytearray) -> None:
            value = bytes(data)
            if self._notification_callback is not None:
                self._notification_callback(value)
            # Bleak invokes this handler on the same loop used by _run().
            # Queueing here lets synchronous callers wait for notifications
            # that arrive after a write operation has completed.
            if self._notification_queue is not None:
                self._notification_queue.put_nowait(value)

        self._run(self._client.start_notify(self.characteristic_uuid, handler))
        self._notifications_started = True

    async def _next_notification(self, timeout: float) -> Optional[bytes]:
        if self._notification_queue is None:
            return None
        try:
            return await asyncio.wait_for(self._notification_queue.get(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            return None

    def wait_for_notification(self, timeout: float = 30.0) -> Optional[bytes]:
        """Wait for the next notification while continuing the BLE event loop."""
        return self._run(self._next_notification(timeout))

    def write(self, payload: bytes, response: bool = True) -> None:
        if self._client is None:
            raise RuntimeError("尚未连接设备")
        self._run_with_timeout(
            self._client.write_gatt_char(self.characteristic_uuid, bytes(payload), response=response),
            self.WRITE_TIMEOUT_SECONDS,
            "写入",
        )

    def disconnect(self) -> None:
        try:
            if self._client is not None:
                self._run(self._client.disconnect())
                self._client = None
            self._notifications_started = False
            self._notification_callback = None
            self._notification_queue = None
        finally:
            self.close()

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()


# Public name used by the implementation plan and CLI.
BleakTransport = BleTransport
