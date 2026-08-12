"""Config flow for TSUN micro-inverters."""

from ipaddress import IPv4Network
import logging
from typing import Any, cast, override

from tsun_local_api import (
    LoggerMetadata,
    TsunClient,
    TsunConnectionError,
    TsunError,
    async_discover_devices,
    async_read_logger_metadata,
    bounded_ipv4_network,
    parse_discovery_network,
)
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import network
from homeassistant.components.network import MDNS_TARGET_IP
from homeassistant.config_entries import ConfigFlowContext, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DISCOVERY_NETWORK,
    CONF_ERROR_SCAN_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_FIRMWARE_VERSION,
    CONF_INVERTER_SN,
    CONF_LOGGER_SN,
    CONF_MAC_ADDRESS,
    CONF_NIGHT_SCAN_INTERVAL,
    DEFAULT_ERROR_SCAN_INTERVAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_NIGHT_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_FAILURE_THRESHOLD,
    MAX_NIGHT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_FAILURE_THRESHOLD,
    MIN_NIGHT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import get_poll_lock

_LOGGER = logging.getLogger(__name__)
_CONTEXT_NETWORKS = "tsun_discovery_networks"
_CONTEXT_PORT = "tsun_discovery_port"
_CONTEXT_EXCLUDED = "tsun_excluded_hosts"
_SOURCE_CONTINUE = "tsun_continue_discovery"


def _connection_schema(
    hosts: list[str] | None = None,
    port: int = DEFAULT_PORT,
    *,
    request_logger_sn: bool = False,
) -> vol.Schema:
    host_field: Any = str
    if hosts:
        host_field = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=host, label=host) for host in hosts],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_HOST): host_field,
        vol.Required(CONF_PORT, default=port): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
    if request_logger_sn:
        schema[vol.Required(CONF_LOGGER_SN)] = vol.All(
            vol.Coerce(int), vol.Range(min=1, max=0xFFFFFFFF)
        )
    return vol.Schema(schema)


def _network_schema(suggested: str | None, port: int) -> vol.Schema:
    network_key = vol.Required(CONF_DISCOVERY_NETWORK)
    if suggested is not None:
        network_key = vol.Required(CONF_DISCOVERY_NETWORK, default=suggested)
    return vol.Schema(
        {
            network_key: str,
            vol.Required(CONF_PORT, default=port): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )


OPTIONS_SCHEMA = vol.Schema(
    {
        # The inverter is solar-powered and fully offline at night. Separate
        # intervals reduce needless failures after the offline threshold.
        # pylint: disable-next=home-assistant-config-flow-polling-field
        vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(CONF_ERROR_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(CONF_NIGHT_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_NIGHT_SCAN_INTERVAL,
                max=MAX_NIGHT_SCAN_INTERVAL,
                step=60,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(CONF_FAILURE_THRESHOLD): NumberSelector(
            NumberSelectorConfig(
                min=MIN_FAILURE_THRESHOLD,
                max=MAX_FAILURE_THRESHOLD,
                step=1,
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)


async def _async_validate(
    hass: HomeAssistant, data: dict[str, Any], metadata: LoggerMetadata
) -> str:
    client = TsunClient(
        data[CONF_HOST],
        data[CONF_LOGGER_SN],
        port=data[CONF_PORT],
        metadata=metadata,
    )
    async with get_poll_lock(hass):
        telemetry = await client.async_read()
    return telemetry.device.model


async def _async_get_networks(hass: HomeAssistant) -> list[IPv4Network]:
    """Return visible networks and networks learned from configured entries."""
    discovered: set[IPv4Network] = set()
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ipv4 in adapter["ipv4"]:
            if candidate := bounded_ipv4_network(
                ipv4["address"], ipv4["network_prefix"]
            ):
                discovered.add(candidate)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if host := entry.data.get(CONF_HOST):
            try:
                candidate = bounded_ipv4_network(str(host), 24)
            except ValueError:
                continue
            if candidate is not None:
                discovered.add(candidate)
    if not discovered:
        source_ip = await network.async_get_source_ip(hass, MDNS_TARGET_IP)
        if candidate := bounded_ipv4_network(source_ip, 24):
            discovered.add(candidate)
    return sorted(discovered, key=lambda item: int(item.network_address))


class TsunConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TSUN."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the TSUN config flow."""
        self._hosts: list[str] | None = None
        self._networks: list[IPv4Network] | None = None
        self._port = DEFAULT_PORT
        self._excluded: set[str] = set()
        self._request_logger_sn = False
        self._suggested_network: str | None = None
        self._continue_after_host: str | None = None

    def _unconfigured(self, hosts: list[str]) -> list[str]:
        configured = {
            str(entry.data[CONF_HOST])
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if CONF_HOST in entry.data
        }
        return [host for host in hosts if host not in configured | self._excluded]

    async def _async_create_device(
        self, user_input: dict[str, Any], *, continue_discovery: bool = False
    ) -> ConfigFlowResult | str:
        data = dict(user_input)
        automatically_detected = CONF_LOGGER_SN not in data
        try:
            metadata = await async_read_logger_metadata(
                async_get_clientsession(self.hass),
                str(data[CONF_HOST]),
                port=int(data[CONF_PORT]),
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while reading TSUN metadata")
            return "unknown"
        if CONF_LOGGER_SN not in data:
            if metadata.logger_sn is None:
                self._request_logger_sn = True
                return "cannot_detect_logger_sn"
            data[CONF_LOGGER_SN] = metadata.logger_sn
        metadata = LoggerMetadata(
            logger_sn=int(data[CONF_LOGGER_SN]),
            inverter_serial_number=metadata.inverter_serial_number,
            firmware_version=metadata.firmware_version,
            mac_address=metadata.mac_address,
        )
        try:
            model = await _async_validate(self.hass, data, metadata)
        except TsunConnectionError:
            return "cannot_connect"
        except TsunError:
            if automatically_detected:
                self._request_logger_sn = True
            return "invalid_response"
        except Exception:
            _LOGGER.exception("Unexpected exception while connecting to TSUN")
            return "unknown"

        unique_id = str(data[CONF_LOGGER_SN])
        await self.async_set_unique_id(unique_id)
        metadata_updates = {
            key: value
            for key, value in {
                CONF_INVERTER_SN: metadata.inverter_serial_number,
                CONF_FIRMWARE_VERSION: metadata.firmware_version,
                CONF_MAC_ADDRESS: metadata.mac_address,
            }.items()
            if value is not None
        }
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data[CONF_PORT],
                **metadata_updates,
            }
        )
        data.update(metadata_updates)
        if continue_discovery:
            self._continue_after_host = str(data[CONF_HOST])
            self._port = int(data[CONF_PORT])
        return self.async_create_entry(
            title=f"{model} ({data[CONF_LOGGER_SN]})", data=data
        )

    @override
    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Offer the next unconfigured device after a discovery-created entry."""
        if self._continue_after_host is None:
            return result
        next_result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context=cast(
                ConfigFlowContext,
                {
                    "source": _SOURCE_CONTINUE,
                    _CONTEXT_NETWORKS: [str(item) for item in self._networks or []],
                    _CONTEXT_PORT: self._port,
                    _CONTEXT_EXCLUDED: sorted(
                        self._excluded | {self._continue_after_host}
                    ),
                },
            ),
        )
        if next_result.get("type") not in {"abort", "create_entry"} and (
            flow_id := next_result.get("flow_id")
        ):
            result["next_flow"] = (config_entries.FlowType.CONFIG_FLOW, flow_id)
        return result

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer manual setup or user-initiated discovery."""
        if self.context.get(_CONTEXT_NETWORKS) is not None:
            self._port = cast(int, self.context.get(_CONTEXT_PORT, DEFAULT_PORT))
            excluded = cast(list[str], self.context.get(_CONTEXT_EXCLUDED, []))
            self._excluded.update(str(host) for host in excluded)
            networks = cast(list[str], self.context.get(_CONTEXT_NETWORKS, []))
            self._networks = [
                parse_discovery_network(str(value))
                for value in networks
            ]
            return await self.async_step_discover()
        return self.async_show_menu(step_id="user", menu_options=["discover", "manual"])

    async def async_step_tsun_continue_discovery(
        self, discovery_info: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Resume discovery after another TSUN entry was created."""
        return await self.async_step_user()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup with automatic SN lookup and fallback."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create_device(user_input)
            if not isinstance(result, str):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(request_logger_sn=self._request_logger_sn),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_discover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover devices by native UDP and bounded network analysis."""
        if self._hosts is None:
            self._networks = self._networks or await _async_get_networks(self.hass)
            if self._networks:
                self._suggested_network = str(self._networks[0])
            try:
                discovered = await async_discover_devices(self._networks, self._port)
            except HomeAssistantError, OSError, RuntimeError, ValueError:
                discovered = []
            self._hosts = self._unconfigured(discovered)
            if not self._hosts:
                if discovered:
                    return self.async_abort(reason="all_devices_configured")
                return await self.async_step_discover_network()

        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create_device(
                user_input, continue_discovery=True
            )
            if not isinstance(result, str):
                return result
            errors["base"] = result
        return self.async_show_form(
            step_id="discover",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(
                    self._hosts,
                    self._port,
                    request_logger_sn=self._request_logger_sn,
                ),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_discover_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept a routed VLAN when it is not visible as a HA adapter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                selected = parse_discovery_network(user_input[CONF_DISCOVERY_NETWORK])
            except ValueError:
                errors["base"] = "invalid_network"
            else:
                self._port = int(user_input[CONF_PORT])
                self._networks = [selected]
                self._suggested_network = str(selected)
                try:
                    discovered = await async_discover_devices(
                        self._networks, self._port
                    )
                except HomeAssistantError, OSError, RuntimeError, ValueError:
                    discovered = []
                self._hosts = self._unconfigured(discovered)
                if self._hosts:
                    return await self.async_step_discover()
                errors["base"] = "no_devices_found"
        return self.async_show_form(
            step_id="discover_network",
            data_schema=_network_schema(self._suggested_network, self._port),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the host and TCP port while retaining the communication SN."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            updated = {**entry.data, **user_input}
            metadata = LoggerMetadata(
                logger_sn=entry.data[CONF_LOGGER_SN],
                inverter_serial_number=entry.data.get(CONF_INVERTER_SN),
                firmware_version=entry.data.get(CONF_FIRMWARE_VERSION),
                mac_address=entry.data.get(CONF_MAC_ADDRESS),
            )
            try:
                await _async_validate(self.hass, updated, metadata)
            except TsunError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception while reconfiguring TSUN")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(entry.data[CONF_LOGGER_SN]))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=user_input,
                    reload_even_if_entry_is_unchanged=False,
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Required(CONF_PORT): vol.All(
                            vol.Coerce(int), vol.Range(min=1, max=65535)
                        ),
                    }
                ),
                entry.data,
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TsunOptionsFlow:
        """Return the polling options flow."""
        return TsunOptionsFlow()


class TsunOptionsFlow(config_entries.OptionsFlow):
    """Configure normal, error and night polling independently."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {
            CONF_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_ERROR_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_ERROR_SCAN_INTERVAL, DEFAULT_ERROR_SCAN_INTERVAL
            ),
            CONF_NIGHT_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_NIGHT_SCAN_INTERVAL, DEFAULT_NIGHT_SCAN_INTERVAL
            ),
            CONF_FAILURE_THRESHOLD: self.config_entry.options.get(
                CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, defaults),
        )
