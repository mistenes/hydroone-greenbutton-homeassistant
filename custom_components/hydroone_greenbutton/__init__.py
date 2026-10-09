"""Automatic hourly Hydro One electricity history for Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import EnergyConverter

import logging

from .client import AuthenticationError, HydroOneClient, PortalError
from .const import (
    CONF_ACCOUNT, CONF_CONTRACT, CONF_HISTORY, CONF_LOOKBACK, CONF_METER,
    CONF_POLL, DEFAULT_HISTORY, DEFAULT_LOOKBACK, DEFAULT_POLL, DOMAIN,
)
from .parser import FeedError, merge_readings, statistics_rows
from .history import export_window

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


class HydroOneCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name="Hydro One Green Button",
            update_interval=timedelta(hours=entry.options.get(CONF_POLL, DEFAULT_POLL)),
        )
        self.entry = entry
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.intervals")
        self.readings: dict[str, str] = {}
        token = sha256(entry.entry_id.encode()).hexdigest()[:16]
        self.statistic_id = f"{DOMAIN}:electricity_{token}"

    async def load(self):
        saved = await self.store.async_load()
        if saved:
            self.readings = saved.get("readings", {})

    async def _async_update_data(self):
        local_zone = ZoneInfo("America/Toronto")
        today = datetime.now(local_zone).date()
        start, end = export_window(
            today, self.readings, self.entry.data.get(CONF_HISTORY, DEFAULT_HISTORY),
            self.entry.options.get(CONF_LOOKBACK, DEFAULT_LOOKBACK),
        )
        try:
            async with HydroOneClient(self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD]) as client:
                await client.login()
                incoming = await client.download(
                    self.entry.data[CONF_ACCOUNT], self.entry.data[CONF_CONTRACT], start, end,
                )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Hydro One login needs attention") from err
        except (PortalError, FeedError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed("Hydro One hourly data could not be refreshed; previous readings are retained") from err

        merged = merge_readings(self.readings, incoming)
        rows = await self.hass.async_add_executor_job(statistics_rows, merged)
        metadata = {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": f"Hydro One electricity ({self.entry.data[CONF_METER]})",
            "source": DOMAIN,
            "statistic_id": self.statistic_id,
            "unit_class": EnergyConverter.UNIT_CLASS,
            "unit_of_measurement": "kWh",
        }
        # Rebuild this integration's own cumulative series. Recorder upserts
        # matching hours, so overlapping downloads and corrections are safe.
        async_add_external_statistics(self.hass, metadata, rows)
        success = datetime.now(timezone.utc)
        await self.store.async_save({"readings": merged, "last_success": success.isoformat()})
        self.readings = merged
        newest = max(map(int, merged))
        return {
            "last_success": success,
            "latest_interval": datetime.fromtimestamp(newest, timezone.utc),
            "latest_kwh": float(merged[str(newest)]),
            "interval_count": len(merged),
            "statistic_id": self.statistic_id,
        }


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})

    async def refresh(call: ServiceCall):
        for coordinator in list(hass.data[DOMAIN].values()):
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "refresh", refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    coordinator = HydroOneCoordinator(hass, entry)
    await coordinator.load()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
