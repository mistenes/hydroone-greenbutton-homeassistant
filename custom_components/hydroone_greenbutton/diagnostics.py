"""Diagnostics contain no credentials, account identifiers or raw XML."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is None:
        return {"loaded": False}
    return {
        "loaded": True,
        "last_update_success": coordinator.last_update_success,
        "imported_hours": len(coordinator.readings),
        "statistic_id": coordinator.statistic_id,
        "options": dict(entry.options),
    }
