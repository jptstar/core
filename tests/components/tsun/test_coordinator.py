"""Tests for adaptive TSUN polling."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from tsun_local_api import Telemetry, TsunConnectionError

from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry


async def test_three_failures_enable_night_behavior(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_tsun_client: AsyncMock,
    telemetry: Telemetry,
) -> None:
    """Keep data through transient failures and go offline at the threshold."""
    from homeassistant.components.tsun.coordinator import TsunDataUpdateCoordinator

    coordinator = TsunDataUpdateCoordinator(
        hass,
        mock_config_entry,
        mock_tsun_client,
        poll_lock=asyncio.Lock(),
        normal_interval=20,
        error_interval=20,
        night_interval=300,
        failure_threshold=3,
    )
    initial = await coordinator._async_update_data()
    coordinator.data = initial
    mock_tsun_client.async_read.side_effect = TsunConnectionError("offline")

    first_failure = await coordinator._async_update_data()
    coordinator.data = first_failure
    assert first_failure.online
    assert coordinator.update_interval == timedelta(seconds=20)

    second_failure = await coordinator._async_update_data()
    coordinator.data = second_failure
    assert second_failure.online

    third_failure = await coordinator._async_update_data()
    assert not third_failure.online
    assert third_failure.telemetry is telemetry
    assert coordinator.update_interval == timedelta(seconds=300)
