from epd_dashboard_sync.protocol import (
    CMD_INIT,
    CMD_SET_SLIDE,
    CMD_SET_SLOT,
    CMD_WRITE_IMG,
    encode_command,
    encode_slot_action,
    encode_slot_index,
    chunk_image,
)
from epd_dashboard_sync.transport import BleTransport


def test_command_framing_is_command_byte_followed_by_payload() -> None:
    assert encode_command(CMD_INIT) == bytes([CMD_INIT])
    assert encode_command(CMD_SET_SLIDE, b"\x01\x02") == bytes([CMD_SET_SLIDE, 1, 2])


def test_set_slot_v2_action_encoding() -> None:
    assert encode_slot_action(0, 13, protocol_v2=True) == bytes([0, 13, 0, 0, 0])
    assert encode_slot_action(3, 13, protocol_v2=False) == bytes([3, 13])


def test_write_img_chunk_has_config_byte_and_respects_mtu() -> None:
    chunks = list(chunk_image(bytes(range(20)), mtu=8))
    assert all(len(chunk) <= 6 for chunk in chunks)
    assert chunks[0][0] & 0x02
    assert chunks[-1][0] & 0x02 == 0


def test_slot_index_supports_legacy_and_v2() -> None:
    assert encode_slot_index(0x1234, protocol_v2=False) == bytes([0x34])
    assert encode_slot_index(0x1234, protocol_v2=True) == bytes([0x34, 0x12, 0, 0])


def test_fake_transport_records_command_writes() -> None:
    from tests.fakes import FakeTransport

    transport = FakeTransport(version=b"v1.10")
    transport.write(encode_command(CMD_WRITE_IMG, b"\x02\xAA"), response=False)
    assert transport.writes == [(bytes([CMD_WRITE_IMG, 2, 0xAA]), False)]
    assert transport.read_version() == b"v1.10"


def test_ble_transport_reuses_one_event_loop_for_a_session() -> None:
    import asyncio

    transport = BleTransport()

    async def current_loop_id():
        return id(asyncio.get_running_loop())

    first = transport._run(current_loop_id())
    second = transport._run(current_loop_id())
    assert first == second
    transport.close()


def test_ble_transport_does_not_start_notifications_twice() -> None:
    class Client:
        def __init__(self):
            self.calls = 0

        async def start_notify(self, characteristic, callback):
            self.calls += 1

    transport = BleTransport()
    client = Client()
    transport._client = client
    transport.start_notifications(lambda value: None)
    transport.start_notifications(lambda value: None)
    assert client.calls == 1
    transport.close()


def test_ble_transport_waits_for_delayed_notification() -> None:
    class Client:
        def __init__(self):
            self.handler = None

        async def start_notify(self, characteristic, callback):
            self.handler = callback

    transport = BleTransport()
    client = Client()
    transport._client = client
    transport.start_notifications(lambda value: None)
    client.handler(None, bytearray(b"ready=1"))
    assert transport.wait_for_notification(timeout=0.1) == b"ready=1"
    transport.close()
