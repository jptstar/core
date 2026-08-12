"""Tests for TSUN diagnostics."""

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

from .conftest import HOST, LOGGER_SN


async def test_diagnostics_redact_identifiers(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_tsun_client: AsyncMock,
) -> None:
    """Network and identity values are absent from downloaded diagnostics."""
    from homeassistant.components.tsun.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(
        hass, mock_config_entry
    )
    serialized = str(diagnostics)
    assert HOST not in serialized
    assert str(LOGGER_SN) not in serialized
    assert diagnostics["privacy"]["ap_envelope_included"] is False
