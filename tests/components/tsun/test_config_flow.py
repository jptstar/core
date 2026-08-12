"""Tests for the TSUN config flow."""

from ipaddress import IPv4Network
from unittest.mock import AsyncMock, patch

import pytest
from tsun_local_api import LoggerMetadata, TsunConnectionError, TsunProtocolError

from homeassistant.components.tsun.const import (
    CONF_DISCOVERY_NETWORK,
    CONF_ERROR_SCAN_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_LOGGER_SN,
    CONF_NIGHT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.common import MockConfigEntry

from .conftest import HOST, LOGGER_SN

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: 8899}
OTHER_HOST = "192.0.2.11"
TEST_NETWORK = IPv4Network("192.168.77.0/24")


async def test_user_flow(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test a complete user flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"TITAN ({LOGGER_SN})"
    assert result["data"][CONF_LOGGER_SN] == LOGGER_SN
    assert result["data"][CONF_HOST] == HOST
    assert result["result"].unique_id == str(LOGGER_SN)
    assert mock_tsun_client.async_read.await_count >= 2


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (TsunConnectionError("cannot connect"), "cannot_connect"),
        (TsunProtocolError("invalid response"), "invalid_response"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
    exception: Exception,
    expected_error: str,
) -> None:
    """Test connection errors and recovery."""
    mock_tsun_client.async_read.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    mock_tsun_client.async_read.side_effect = None
    recovery_input = USER_INPUT
    if expected_error == "invalid_response":
        recovery_input = {**USER_INPUT, CONF_LOGGER_SN: LOGGER_SN}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], recovery_input
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_manual_logger_sn_fallback(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test manual SN input is requested only when automatic reading fails."""
    from homeassistant.components.tsun import config_flow

    config_flow.async_read_logger_metadata.return_value = LoggerMetadata()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_detect_logger_sn"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_LOGGER_SN: LOGGER_SN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_updates_address(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that a duplicate logger updates its local address and aborts."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**USER_INPUT, CONF_HOST: "192.0.2.20"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "192.0.2.20"


async def test_metadata_unknown_error_and_recovery(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test an unexpected metadata error is shown and can be retried."""
    from homeassistant.components.tsun import config_flow

    config_flow.async_read_logger_metadata.side_effect = RuntimeError("unexpected")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

    config_flow.async_read_logger_metadata.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_discovery_adds_devices_one_after_another(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test each discovery-created entry offers the next TSUN device."""
    from homeassistant.components.tsun import config_flow

    with (
        patch.object(
            config_flow,
            "_async_get_networks",
            AsyncMock(return_value=[TEST_NETWORK]),
        ),
        patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(return_value=[HOST, OTHER_HOST]),
        ),
    ):

        async def metadata_for_host(*args, **kwargs) -> LoggerMetadata:
            host = args[1]
            return LoggerMetadata(
                logger_sn=LOGGER_SN if host == HOST else LOGGER_SN + 1
            )

        config_flow.async_read_logger_metadata.side_effect = metadata_for_host
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "discover"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 8899}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_HOST] == HOST
        assert "next_flow" in result

        _, next_flow_id = result["next_flow"]
        result = await hass.config_entries.flow.async_configure(
            next_flow_id, {CONF_HOST: OTHER_HOST, CONF_PORT: 8899}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_LOGGER_SN] == LOGGER_SN + 1
        assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_discovery_aborts_when_all_devices_are_configured(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test discovery ignores devices already represented by entries."""
    from homeassistant.components.tsun import config_flow

    mock_config_entry.add_to_hass(hass)
    with (
        patch.object(
            config_flow,
            "_async_get_networks",
            AsyncMock(return_value=[TEST_NETWORK]),
        ),
        patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(return_value=[HOST]),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_devices_configured"


async def test_discovery_failure_offers_routed_network(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test a discovery failure falls back to the routed-network form."""
    from homeassistant.components.tsun import config_flow

    with (
        patch.object(
            config_flow,
            "_async_get_networks",
            AsyncMock(return_value=[TEST_NETWORK]),
        ),
        patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(side_effect=RuntimeError("discovery failed")),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discover_network"


async def test_discovered_device_error_and_recovery(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test an error on a discovered device is shown and can be retried."""
    from homeassistant.components.tsun import config_flow

    with (
        patch.object(
            config_flow,
            "_async_get_networks",
            AsyncMock(return_value=[TEST_NETWORK]),
        ),
        patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(return_value=[HOST]),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )
        mock_tsun_client.async_read.side_effect = TsunConnectionError("offline")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 8899}
        )
        assert result["errors"] == {"base": "cannot_connect"}

        mock_tsun_client.async_read.side_effect = None
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 8899}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_routed_network_validation_and_discovery(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test invalid and valid routed VLAN input and an empty retry."""
    from homeassistant.components.tsun import config_flow

    discover = AsyncMock(return_value=[])
    with (
        patch.object(config_flow, "_async_get_networks", AsyncMock(return_value=[])),
        patch.object(config_flow, "async_discover_devices", discover),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )
        assert result["step_id"] == "discover_network"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DISCOVERY_NETWORK: "203.0.113.0/24", CONF_PORT: 8899},
        )
        assert result["errors"] == {"base": "invalid_network"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DISCOVERY_NETWORK: "192.168.44.0/24", CONF_PORT: 8899},
        )
        assert result["errors"] == {"base": "no_devices_found"}

        discover.side_effect = OSError("scan failed")
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DISCOVERY_NETWORK: "192.168.44.0/24", CONF_PORT: 8899},
        )
        assert result["errors"] == {"base": "no_devices_found"}

        discover.side_effect = None
        discover.return_value = [HOST]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DISCOVERY_NETWORK: "192.168.44.0/24", CONF_PORT: 8899},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discover"


async def test_continuation_source_restores_discovery_context(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
) -> None:
    """Test the internal continuation source restores networks and exclusions."""
    from homeassistant.components.tsun import config_flow

    with patch.object(
        config_flow,
        "async_discover_devices",
        AsyncMock(return_value=[HOST, OTHER_HOST]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": "tsun_continue_discovery",
                "tsun_discovery_networks": [str(TEST_NETWORK)],
                "tsun_discovery_port": 8899,
                "tsun_excluded_hosts": [HOST],
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discover"


async def test_visible_and_learned_networks(
    hass: HomeAssistant,
) -> None:
    """Test discovery networks come from HA adapters and configured devices."""
    from homeassistant.components.tsun import config_flow

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "10.20.30.40", CONF_PORT: 8899, CONF_LOGGER_SN: LOGGER_SN},
        unique_id=str(LOGGER_SN),
    )
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "not-an-address", CONF_PORT: 8899, CONF_LOGGER_SN: 2},
        unique_id="2",
    ).add_to_hass(hass)
    adapters = [
        {
            "enabled": True,
            "ipv4": [{"address": "192.168.50.2", "network_prefix": 24}],
        },
        {
            "enabled": False,
            "ipv4": [{"address": "192.168.60.2", "network_prefix": 24}],
        },
    ]
    with patch.object(
        config_flow.network,
        "async_get_adapters",
        AsyncMock(return_value=adapters),
    ):
        networks = await config_flow._async_get_networks(hass)

    assert networks == [
        IPv4Network("10.20.30.0/24"),
        IPv4Network("192.168.50.0/24"),
    ]


async def test_source_ip_network_fallback(hass: HomeAssistant) -> None:
    """Test the HA source address is used when no adapter network is visible."""
    from homeassistant.components.tsun import config_flow

    with (
        patch.object(
            config_flow.network,
            "async_get_adapters",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            config_flow.network,
            "async_get_source_ip",
            AsyncMock(return_value="192.168.70.2"),
        ),
    ):
        networks = await config_flow._async_get_networks(hass)

    assert networks == [IPv4Network("192.168.70.0/24")]


async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reconfiguration keeps identity and updates the address."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: 8999}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == OTHER_HOST
    assert mock_config_entry.data[CONF_PORT] == 8999
    assert mock_config_entry.data[CONF_LOGGER_SN] == LOGGER_SN


@pytest.mark.parametrize(
    ("exception", "expected_error"),
    [
        (TsunConnectionError("cannot connect"), "cannot_connect"),
        (TsunProtocolError("invalid response"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_reconfigure_errors_and_recovery(
    hass: HomeAssistant,
    mock_tsun_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    exception: Exception,
    expected_error: str,
) -> None:
    """Test reconfiguration errors are shown and can be retried."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    mock_tsun_client.async_read.side_effect = exception
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: 8899}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    mock_tsun_client.async_read.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: OTHER_HOST, CONF_PORT: 8899}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_options_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test all independent polling options are stored."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    options = {
        CONF_SCAN_INTERVAL: 30,
        CONF_ERROR_SCAN_INTERVAL: 45,
        CONF_NIGHT_SCAN_INTERVAL: 600,
        CONF_FAILURE_THRESHOLD: 5,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == options
