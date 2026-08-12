"""Sensors for TSUN micro-inverters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TsunConfigEntry
from .const import CONF_LOGGER_SN
from .coordinator import TsunDataUpdateCoordinator
from .entity import TsunEntity


@dataclass(frozen=True, kw_only=True)
class TsunSensorEntityDescription(SensorEntityDescription):
    """Describe a TSUN sensor."""

    keep_available_offline: bool = False
    register_address: str | None = None


def _measurement(
    key: str,
    device_class: SensorDeviceClass,
    unit: str,
    precision: int,
    *,
    state_class: SensorStateClass = SensorStateClass.MEASUREMENT,
    keep_available_offline: bool = False,
) -> TsunSensorEntityDescription:
    return TsunSensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=state_class,
        suggested_display_precision=precision,
        keep_available_offline=keep_available_offline,
    )


def _raw_alarm(key: str, address: str) -> TsunSensorEntityDescription:
    return TsunSensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        register_address=address,
    )


SENSORS: tuple[TsunSensorEntityDescription, ...] = (
    _measurement(
        "ac_voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1
    ),
    _measurement(
        "ac_current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 2
    ),
    _measurement(
        "ac_frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ, 2
    ),
    _measurement("ac_power", SensorDeviceClass.POWER, UnitOfPower.WATT, 1),
    _measurement("dc_power_total", SensorDeviceClass.POWER, UnitOfPower.WATT, 1),
    _measurement(
        "ac_energy_today",
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.KILO_WATT_HOUR,
        2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        keep_available_offline=True,
    ),
    _measurement(
        "ac_energy_total",
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.KILO_WATT_HOUR,
        2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="communication_last_success",
        translation_key="communication_last_success",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="communication_duration",
        translation_key="communication_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="communication_blocks",
        translation_key="communication_blocks",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="communication_failures",
        translation_key="communication_failures",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="logger_sn",
        translation_key="logger_sn",
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="inverter_serial_number",
        translation_key="inverter_serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    TsunSensorEntityDescription(
        key="mac_address",
        translation_key="mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        keep_available_offline=True,
    ),
    *(
        _raw_alarm(f"alarm_global_{index}_raw", f"0x{0x0BBB + index:04X}")
        for index in range(4)
    ),
    *(
        _raw_alarm(f"alarm_secondary_{index}_raw", f"0x{0x0CE4 + index:04X}")
        for index in range(4)
    ),
    *(
        _raw_alarm(f"alarm_code_{index}_raw", f"0x{0x3002 + index:04X}")
        for index in range(1, 5)
    ),
)

_PV_ALARM_REGISTERS = (0x0E16, 0x0E1D, 0x0E24, 0x0EDE, 0x0EE5, 0x0EEC)


def _pv_sensors(pv_count: int) -> tuple[TsunSensorEntityDescription, ...]:
    descriptions: list[TsunSensorEntityDescription] = []
    for number in range(1, pv_count + 1):
        for suffix, device_class, unit, state_class, precision, retained in (
            (
                "voltage",
                SensorDeviceClass.VOLTAGE,
                UnitOfElectricPotential.VOLT,
                SensorStateClass.MEASUREMENT,
                1,
                False,
            ),
            (
                "current",
                SensorDeviceClass.CURRENT,
                UnitOfElectricCurrent.AMPERE,
                SensorStateClass.MEASUREMENT,
                2,
                False,
            ),
            (
                "power",
                SensorDeviceClass.POWER,
                UnitOfPower.WATT,
                SensorStateClass.MEASUREMENT,
                1,
                False,
            ),
            (
                "energy_today",
                SensorDeviceClass.ENERGY,
                UnitOfEnergy.KILO_WATT_HOUR,
                SensorStateClass.TOTAL_INCREASING,
                2,
                True,
            ),
            (
                "energy_total",
                SensorDeviceClass.ENERGY,
                UnitOfEnergy.KILO_WATT_HOUR,
                SensorStateClass.TOTAL_INCREASING,
                2,
                True,
            ),
        ):
            key = f"pv{number}_{suffix}"
            descriptions.append(
                TsunSensorEntityDescription(
                    key=key,
                    translation_key=suffix,
                    translation_placeholders={"input": str(number)},
                    device_class=device_class,
                    native_unit_of_measurement=unit,
                    state_class=state_class,
                    suggested_display_precision=precision,
                    keep_available_offline=retained,
                )
            )
        if number <= len(_PV_ALARM_REGISTERS):
            descriptions.append(
                _raw_alarm(
                    f"pv{number}_alarm_raw",
                    f"0x{_PV_ALARM_REGISTERS[number - 1]:04X}",
                )
            )
    return tuple(descriptions)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TsunConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all measurements supported by the detected protocol."""
    coordinator = entry.runtime_data
    added_keys: set[str] = set()
    always_available = {
        "communication_last_success",
        "communication_duration",
        "communication_blocks",
        "communication_failures",
        "logger_sn",
        "inverter_serial_number",
        "firmware_version",
        "mac_address",
    }

    @callback
    def async_add_discovered_entities() -> None:
        """Add alarm or PV entities exposed after a later successful poll."""
        values = coordinator.data.telemetry.values
        descriptions = [
            description
            for description in (
                *SENSORS,
                *_pv_sensors(coordinator.data.telemetry.device.pv_count),
            )
            if description.key not in added_keys
            and (description.key in values or description.key in always_available)
        ]
        if not descriptions:
            return
        added_keys.update(description.key for description in descriptions)
        async_add_entities(
            TsunSensor(coordinator, entry, description)
            for description in descriptions
        )

    async_add_discovered_entities()
    entry.async_on_unload(
        coordinator.async_add_listener(async_add_discovered_entities)
    )


class TsunSensor(TsunEntity, SensorEntity):
    """A TSUN sensor."""

    entity_description: TsunSensorEntityDescription

    def __init__(
        self,
        coordinator: TsunDataUpdateCoordinator,
        entry: TsunConfigEntry,
        description: TsunSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.data.telemetry.device.logger_sn}_{description.key}"
        )

    @property
    @override
    def suggested_object_id(self) -> str:
        """Keep technical entity identifiers in English for every UI language."""
        return self.entity_description.key

    @property
    def native_value(self) -> Any:
        """Return the latest measurement, communication or identity value."""
        data = self.coordinator.data
        device = data.telemetry.device
        key = self.entity_description.key
        special = {
            "communication_last_success": data.last_success,
            "communication_duration": data.duration_ms,
            "communication_blocks": data.blocks_ok,
            "communication_failures": data.consecutive_failures,
            "logger_sn": str(device.logger_sn),
            "inverter_serial_number": device.inverter_serial_number,
            "firmware_version": device.firmware_version,
            "mac_address": device.mac_address,
        }
        return special[key] if key in special else data.telemetry.values.get(key)

    @property
    def available(self) -> bool:
        """Retain counters and diagnostics while live readings sleep at night."""
        if not super().available:
            return False
        if self.native_value is None:
            return False
        return (
            self.coordinator.data.online
            or self.entity_description.keep_available_offline
        )

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the source register and hexadecimal raw alarm value."""
        if self.entity_description.register_address is None:
            return None
        value = self.native_value
        return {
            "register_address": self.entity_description.register_address,
            "raw_hex": f"0x{int(value):04X}" if isinstance(value, int) else "unknown",
        }
