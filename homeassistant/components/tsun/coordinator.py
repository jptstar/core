"""Data update coordinator for TSUN micro-inverters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from tsun_local_api import (
    Telemetry,
    TsunClient,
    TsunError,
    safe_error_details,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_POLL_LOCK = "poll_lock"


def get_poll_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return one lock shared by all configured TSUN micro-inverters."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(_POLL_LOCK, asyncio.Lock())


@dataclass(frozen=True, slots=True)
class TsunCoordinatorData:
    """Latest telemetry plus communication state."""

    telemetry: Telemetry
    online: bool
    last_success: datetime
    duration_ms: int
    blocks_ok: int
    consecutive_failures: int
    last_error: dict[str, str] | None = None


class TsunDataUpdateCoordinator(DataUpdateCoordinator[TsunCoordinatorData]):
    """Coordinate adaptive polling for one TSUN micro-inverter."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: TsunClient,
        *,
        poll_lock: asyncio.Lock,
        normal_interval: int,
        error_interval: int,
        night_interval: int,
        failure_threshold: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=normal_interval),
        )
        self.client = client
        self._poll_lock = poll_lock
        self._normal_interval = timedelta(seconds=normal_interval)
        self._error_interval = timedelta(seconds=error_interval)
        self._night_interval = timedelta(seconds=night_interval)
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0

    @property
    def diagnostic_summary(self) -> dict[str, Any]:
        """Return privacy-safe communication diagnostics."""
        data = self.data
        return {
            "online": data.online if data is not None else None,
            "last_success": (
                data.last_success.isoformat() if data is not None else None
            ),
            "last_duration_ms": data.duration_ms if data is not None else None,
            "last_blocks_ok": data.blocks_ok if data is not None else None,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self._failure_threshold,
            "normal_polling_seconds": int(self._normal_interval.total_seconds()),
            "error_polling_seconds": int(self._error_interval.total_seconds()),
            "night_polling_seconds": int(self._night_interval.total_seconds()),
            "last_error": data.last_error if data is not None else None,
        }

    async def _async_update_data(self) -> TsunCoordinatorData:
        """Fetch telemetry and adapt the next polling interval."""
        try:
            # Local loggers are small devices. Complete exchanges are serialized
            # so multiple configured inverters are never polled simultaneously.
            async with self._poll_lock:
                telemetry = await self.client.async_read()
        except TsunError as err:
            if self.data is None:
                raise UpdateFailed("Unable to update TSUN telemetry") from err

            self._consecutive_failures += 1
            threshold_reached = self._consecutive_failures >= self._failure_threshold
            self.update_interval = (
                self._night_interval if threshold_reached else self._error_interval
            )
            error_details = safe_error_details(err)
            if trace := self.client.diagnostic_trace:
                error_details["protocol"] = str(trace[-1].get("protocol", "unknown"))
                error_details["stage"] = str(trace[-1].get("stage", "unknown"))
            if threshold_reached and self.data.online:
                _LOGGER.warning(
                    "TSUN micro-inverter is unavailable after %s consecutive "
                    "communication failures; night polling interval enabled",
                    self._consecutive_failures,
                )
            return replace(
                self.data,
                online=False if threshold_reached else self.data.online,
                duration_ms=0,
                blocks_ok=0,
                consecutive_failures=self._consecutive_failures,
                last_error=error_details,
            )

        if self.data is not None and not self.data.online:
            _LOGGER.info("TSUN micro-inverter communication restored")
        self._consecutive_failures = 0
        self.update_interval = self._normal_interval
        now = dt_util.utcnow()
        return TsunCoordinatorData(
            telemetry=telemetry,
            online=True,
            last_success=now,
            duration_ms=telemetry.duration_ms,
            blocks_ok=telemetry.blocks_ok,
            consecutive_failures=0,
        )
