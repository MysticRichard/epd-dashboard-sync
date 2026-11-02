from datetime import datetime

from epd_dashboard_sync.clock import CLOCK_CHARACTERISTIC_UUID, CLOCK_MODEL_UUID, encode_set_time, sync_clock


class FakeClockClient:
    instances = []

    def __init__(self, identifier, timeout):
        self.identifier = identifier
        self.timeout = timeout
        self.writes = []
        self.disconnected = False
        self.model = b"DA14585"
        self.connect_attempts = 0
        FakeClockClient.instances.append(self)

    def connect(self):
        self.connect_attempts += 1
        if self.connect_attempts == 1:
            raise RuntimeError("not ready")

    def read_gatt_char(self, uuid):
        assert uuid == CLOCK_MODEL_UUID
        return self.model

    def write_gatt_char(self, uuid, payload, response):
        assert uuid == CLOCK_CHARACTERISTIC_UUID
        self.writes.append((bytes(payload), response))

    def disconnect(self):
        self.disconnected = True


class AsyncClockClient(FakeClockClient):
    async def connect(self):
        self.connect_attempts += 1
        if self.connect_attempts == 1:
            raise RuntimeError("not ready")

    async def read_gatt_char(self, uuid):
        return super().read_gatt_char(uuid)

    async def write_gatt_char(self, uuid, payload, response):
        super().write_gatt_char(uuid, payload, response)

    async def disconnect(self):
        self.disconnected = True


def test_encode_set_time_includes_calendar_fields():
    assert encode_set_time(
        0x6ADEF1C8,
        year=2026,
        month=10,
        day=26,
        weekday=1,
    ) == bytes.fromhex("dd6adef1c807ea0a1a01")


def test_sync_clock_retries_and_only_writes_supported_model():
    FakeClockClient.instances.clear()
    sync_clock(
        "clock-1",
        datetime.fromisoformat("2026-10-26T14:23:04+08:00"),
        client_factory=FakeClockClient,
        sleep=lambda _: None,
    )
    client = FakeClockClient.instances[0]
    assert client.connect_attempts == 2
    assert client.writes == [
        (bytes.fromhex("dd6adef1c807ea0a1a01"), True),
        (bytes.fromhex("e2"), True),
    ]
    assert client.disconnected is True


def test_sync_clock_supports_bleak_async_client():
    AsyncClockClient.instances.clear()
    sync_clock(
        "clock-async",
        datetime.fromisoformat("2026-10-26T14:23:04+08:00"),
        client_factory=AsyncClockClient,
        sleep=lambda _: None,
    )
    client = AsyncClockClient.instances[0]
    assert client.connect_attempts == 2
    assert len(client.writes) == 2
    assert client.disconnected is True
