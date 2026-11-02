from epd_dashboard_sync.config import DeviceConfig
from epd_dashboard_sync.device import _parse_mtu, discover_device, read_slot_inventory
from epd_dashboard_sync.profiles import FOUR_TWO_PROFILE


class DiscoveryTransport:
    def __init__(self, driver=0x05):
        self.driver = driver
        self.writes = []
        self.destructive_writes = []
        self.callback = None

    def read_version(self):
        return b"v1.10"

    def start_notifications(self, callback):
        self.callback = callback

    def write(self, payload, response=True):
        payload = bytes(payload)
        self.writes.append((payload, response))
        if payload == bytes([0x35]):
            self.callback(b"slots=15 0 15 3 2 1048576 fp0 fp1 fp2")
        elif payload == bytes([0x01]):
            # 19-byte config: driver id at byte 7, as defined by the web UI.
            self.callback(bytes([0, 1, 2, 3, 4, 5, 6, self.driver, 0, 0, 7, 8, 9, 10, 1, 0, 0, 0, 0]))
            self.callback(b"mtu=247")


class DelayedSlotsTransport(DiscoveryTransport):
    def __init__(self, driver=0x05):
        super().__init__(driver=driver)
        self.pending_notifications = []

    def write(self, payload, response=True):
        payload = bytes(payload)
        self.writes.append((payload, response))
        if payload == bytes([0x35]):
            self.pending_notifications.append(b"slots=15 0 15 3 2 1048576 fp0 fp1 fp2")
        elif payload == bytes([0x01]):
            self.callback(bytes([0, 1, 2, 3, 4, 5, 6, self.driver, 0, 0, 7, 8, 9, 10, 1, 0, 0, 0, 0]))
            self.callback(b"mtu=247")

    def wait_for_notification(self, timeout=8):
        del timeout
        return self.pending_notifications.pop(0) if self.pending_notifications else None


def test_discover_reads_version_and_config_without_write():
    transport = DiscoveryTransport()
    profile = discover_device(transport, DeviceConfig())
    assert profile.screen_profile == FOUR_TWO_PROFILE
    assert profile.width == 400
    assert profile.height == 300
    assert profile.color_mode == "four_color"
    assert profile.protocol_version == "v2"
    assert profile.mtu == 247
    assert transport.destructive_writes == []
    assert [payload for payload, _ in transport.writes] == [bytes([0x35]), bytes([0x01])]


def test_inventory_parses_v2_slots_message():
    transport = DiscoveryTransport()
    profile = discover_device(transport, DeviceConfig())
    # Inventory reader is read-only and can be used after notifications are set.
    inventory = read_slot_inventory(transport, profile)
    assert inventory.count == 15
    assert inventory.flash_size == 1048576
    assert inventory.selected_slot == 2
    assert inventory.used_slots == (0, 1)


def test_discover_supports_confirmed_4_2_three_color_driver():
    transport = DiscoveryTransport(driver=0x03)
    profile = discover_device(transport, DeviceConfig())
    assert profile.driver_id == "03"
    assert profile.width == 400
    assert profile.height == 300
    assert profile.color_mode == "three_color"


def test_parse_mtu_from_combined_init_notification():
    assert _parse_mtu([b"mtu=244 rle=1 slot_stream=1 clock_font=1 cf2=1"]) == 244


def test_inventory_waits_for_delayed_slots_message():
    transport = DelayedSlotsTransport()
    profile = discover_device(transport, DeviceConfig())
    inventory = read_slot_inventory(transport, profile)
    assert inventory.count == 15
