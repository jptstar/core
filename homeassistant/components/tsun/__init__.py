"""The TSUN integration."""

from tsun_local_api import LoggerMetadata, TsunClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ERROR_SCAN_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_FIRMWARE_VERSION,
    CONF_INVERTER_SN,
    CONF_LOGGER_SN,
    CONF_MAC_ADDRESS,
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_ERROR_SCAN_INTERVAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_NIGHT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)
from .coordinator import TsunDataUpdateCoordinator, get_poll_lock

PLATFORMS = (Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR)

type TsunConfigEntry = ConfigEntry[TsunDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TsunConfigEntry) -> bool:
    """Set up TSUN from a config entry."""
    metadata = LoggerMetadata(
        logger_sn=entry.data[CONF_LOGGER_SN],
        inverter_serial_number=entry.data.get(CONF_INVERTER_SN),
        firmware_version=entry.data.get(CONF_FIRMWARE_VERSION),
        mac_address=entry.data.get(CONF_MAC_ADDRESS),
    )
    client = TsunClient(
        entry.data[CONF_HOST],
        entry.data[CONF_LOGGER_SN],
        port=entry.data[CONF_PORT],
        metadata=metadata,
    )
    coordinator = TsunDataUpdateCoordinator(
        hass,
        entry,
        client,
        poll_lock=get_poll_lock(hass),
        normal_interval=int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
        error_interval=int(
            entry.options.get(CONF_ERROR_SCAN_INTERVAL, DEFAULT_ERROR_SCAN_INTERVAL)
        ),
        night_interval=int(
            entry.options.get(CONF_NIGHT_SCAN_INTERVAL, DEFAULT_NIGHT_SCAN_INTERVAL)
        ),
        failure_threshold=int(
            entry.options.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD)
        ),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TsunConfigEntry) -> bool:
    """Unload a TSUN config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: TsunConfigEntry) -> None:
    """Reload an entry after its connection or polling options change."""
    await hass.config_entries.async_reload(entry.entry_id)
