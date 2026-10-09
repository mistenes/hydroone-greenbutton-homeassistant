"""Diagnostic sensors; hourly energy goes directly into external statistics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_METER, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        HydroOneSensor(coordinator, entry, "last_success", "Last successful update", SensorDeviceClass.TIMESTAMP),
        HydroOneSensor(coordinator, entry, "latest_interval", "Latest available hour", SensorDeviceClass.TIMESTAMP),
        HydroOneSensor(coordinator, entry, "latest_kwh", "Latest hour consumption", SensorDeviceClass.ENERGY),
        HydroOneSensor(coordinator, entry, "interval_count", "Imported hours"),
    ])


class HydroOneSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, key, name, device_class=None):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        if key == "latest_kwh":
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_suggested_display_precision = 3
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Hydro One {entry.data[CONF_METER]}",
            "manufacturer": "Hydro One",
            "model": "Green Button hourly history",
        }

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key) if self.coordinator.data else None

    @property
    def extra_state_attributes(self):
        if self.key == "interval_count":
            return {"energy_statistic_id": self.coordinator.statistic_id}
        return None
