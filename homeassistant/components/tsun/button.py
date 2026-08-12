"""Button entities for TSUN micro-inverters."""

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TsunConfigEntry
from .coordinator import TsunDataUpdateCoordinator
from .entity import TsunEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TsunConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the manual refresh button."""
    async_add_entities([TsunRefreshButton(entry.runtime_data, entry)])


class TsunRefreshButton(TsunEntity, ButtonEntity):
    """Request one immediate complete read from this micro-inverter."""

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "refresh_data"

    def __init__(
        self, coordinator: TsunDataUpdateCoordinator, entry: TsunConfigEntry
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = (
            f"{coordinator.data.telemetry.device.logger_sn}_refresh_data"
        )

    @property
    @override
    def suggested_object_id(self) -> str:
        return "refresh_data"

    async def async_press(self) -> None:
        """Request an update without bypassing coordinator debouncing."""
        await self.coordinator.async_request_refresh()
