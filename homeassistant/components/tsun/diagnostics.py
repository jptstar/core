"""Privacy-safe diagnostics for TSUN micro-inverters."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import TsunConfigEntry
from .const import CONF_INVERTER_SN, CONF_LOGGER_SN, CONF_MAC_ADDRESS

TO_REDACT = {CONF_HOST, CONF_LOGGER_SN, CONF_INVERTER_SN, CONF_MAC_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TsunConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without private identifiers or the AP envelope."""
    coordinator = entry.runtime_data
    data = coordinator.data
    device = data.telemetry.device
    return {
        "config_entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "model_family": device.model,
            "protocol": device.protocol,
            "pv_count": device.pv_count,
            "measurement_keys": sorted(data.telemetry.values),
            "firmware_available": device.firmware_version is not None,
        },
        "communication": coordinator.diagnostic_summary,
        "measurements": {
            key: value
            for key, value in data.telemetry.values.items()
            if key not in {CONF_INVERTER_SN, CONF_MAC_ADDRESS}
        },
        "protocol_trace": list(coordinator.client.diagnostic_trace),
        "privacy": {
            "network_address_included": False,
            "logger_sn_included": False,
            "mac_address_included": False,
            "inverter_serial_number_included": False,
            "ap_envelope_included": False,
        },
    }
