"""Binary sensors for TSUN micro-inverters."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
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
    """Set up connectivity and alarm state."""
    async_add_entities(
        [
            TsunConnectivitySensor(entry.runtime_data, entry),
            TsunAlarmSensor(entry.runtime_data, entry),
        ]
    )


class TsunConnectivitySensor(TsunEntity, BinarySensorEntity):
    """Report whether the micro-inverter answered within the threshold."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(
        self, coordinator: TsunDataUpdateCoordinator, entry: TsunConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = (
            f"{coordinator.data.telemetry.device.logger_sn}_online"
        )

    @property
    @override
    def suggested_object_id(self) -> str:
        return "online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.online


class TsunAlarmSensor(TsunEntity, BinarySensorEntity):
    """Report whether a complete raw alarm set contains a non-zero value."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "inverter_alarm"

    def __init__(
        self, coordinator: TsunDataUpdateCoordinator, entry: TsunConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = (
            f"{coordinator.data.telemetry.device.logger_sn}_inverter_alarm"
        )

    @property
    @override
    def suggested_object_id(self) -> str:
        return "inverter_alarm"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.telemetry.values.get("alarm_active"))

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.online
            and "alarm_active" in self.coordinator.data.telemetry.values
        )

    @property
    def extra_state_attributes(self) -> dict[str, dict[str, int]]:
        active_values = {
            key: value
            for key, value in self.coordinator.data.telemetry.values.items()
            if isinstance(value, int)
            and value != 0
            and key != "alarm_active"
            and (key.endswith("_raw") or key.endswith("_alarm_raw"))
        }
        return {"active_raw_values": active_values}
