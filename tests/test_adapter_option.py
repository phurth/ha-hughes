"""Bluetooth adapter choice: options flow and the pinned connect."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_hughes.const import CONF_ADAPTER, CONF_ADAPTER_FALLBACK, DOMAIN
from custom_components.ha_hughes.coordinator import HughesCoordinator

ADDRESS = "AA:BB:CC:DD:EE:01"
HCI0 = "11:11:11:11:11:11"
PROXY = "33:33:33:33:33:33"


def _heard(source, name, rssi):
    return SimpleNamespace(
        scanner=SimpleNamespace(source=source, name=name),
        advertisement=SimpleNamespace(rssi=rssi),
    )


def _entry(options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={"address": ADDRESS, "device_name": "WD_E8_test", "generation": "gen1"},
        options=options or {},
    )


async def test_options_flow_lists_adapters_and_saves_the_choice(hass) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    heard = [_heard(HCI0, f"hci0 ({HCI0})", -70), _heard(PROXY, f"proxy ({PROXY})", -50)]

    with patch(
        "custom_components.ha_hughes.config_flow.async_scanner_devices_by_address",
        return_value=heard,
    ), patch(
        "custom_components.ha_hughes.config_flow.bluetooth.async_current_scanners",
        return_value=[SimpleNamespace(source="44:44:44:44:44:44", name="hci1", connectable=True)],
        create=True,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    selector = result["data_schema"].schema[next(k for k in result["data_schema"].schema if k == CONF_ADAPTER)]
    values = [o["value"] for o in selector.config["options"]]
    # Heard scanners first (strongest first), then other known scanners.
    assert values == ["automatic", PROXY, HCI0, "44:44:44:44:44:44"]

    with patch("custom_components.ha_hughes.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_ADAPTER: HCI0, CONF_ADAPTER_FALLBACK: False}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ADAPTER: HCI0, CONF_ADAPTER_FALLBACK: False}


async def _connect(hass, options):
    entry = _entry(options)
    entry.add_to_hass(hass)
    coordinator = HughesCoordinator(hass, entry)
    client = SimpleNamespace(_connected_scanner=SimpleNamespace(source=PROXY), _backend=None)
    heard = [_heard(HCI0, "hci0", -70), _heard(PROXY, "proxy", -50)]
    via = AsyncMock(return_value=client)

    class _Stop(Exception):
        pass

    async def stop(*args, **kwargs):  # everything after the connect is out of scope here
        raise _Stop

    with patch("custom_components.ha_hughes.coordinator.bluetooth") as bt, \
         patch("custom_components.ha_hughes.coordinator.establish_connection_via", via), \
         patch("custom_components.ha_hughes.coordinator.asyncio.sleep", stop):
        bt.async_ble_device_from_address.return_value = object()
        bt.async_scanner_devices_by_address.return_value = heard
        bt.async_scanner_by_source.return_value = SimpleNamespace(name=f"proxy ({PROXY})")
        try:
            await coordinator.async_connect()
        except _Stop:
            pass
    return coordinator, via.await_args.kwargs, heard


async def test_default_is_automatic(hass) -> None:
    coordinator, kwargs, _ = await _connect(hass, {})
    assert kwargs["adapter"] == "automatic" and kwargs["allow_fallback"] is True
    assert coordinator.bluetooth_path == f"proxy ({PROXY})"
    assert coordinator.bluetooth_path_source == PROXY


async def test_connect_uses_the_stored_choice(hass) -> None:
    _, kwargs, heard = await _connect(hass, {CONF_ADAPTER: HCI0, CONF_ADAPTER_FALLBACK: False})
    assert kwargs["adapter"] == HCI0
    assert kwargs["allow_fallback"] is False
    assert kwargs["scanner_devices"] == heard


async def test_disconnect_clears_the_path(hass) -> None:
    coordinator, _, _ = await _connect(hass, {})
    coordinator._schedule_reconnect = lambda *args: None
    coordinator._on_disconnect(None)
    assert coordinator.bluetooth_path is None and coordinator.bluetooth_path_source is None


async def test_loads_with_an_older_library_copy(hass, monkeypatch, caplog) -> None:
    """An older ha-ble-pinning loaded earlier in the run must not stop the integration."""
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    old = types.ModuleType("ha_ble_pinning")
    old.connection_path = lambda client: SimpleNamespace(source=None)  # 0.1.0 API only
    monkeypatch.setitem(sys.modules, "ha_ble_pinning", old)
    import custom_components.ha_hughes.pinning as pinning  # noqa: PLC0415

    pinning = importlib.reload(pinning)
    try:
        assert pinning.PINNING_READY is False
        assert list(pinning.adapter_options([], current=HCI0)) == ["automatic", HCI0]
        plain = AsyncMock(return_value="client")
        with patch("bleak_retry_connector.establish_connection", plain):
            client = await pinning.establish_connection_via(object(), ADDRESS, adapter=HCI0)
        assert client == "client"
        assert "Restart Home Assistant once" in caplog.text
    finally:
        monkeypatch.undo()
        importlib.reload(pinning)
    assert pinning.PINNING_READY is True
