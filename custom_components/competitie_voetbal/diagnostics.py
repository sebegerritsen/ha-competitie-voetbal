"""Diagnostics: the account (email and token redacted), its teams and what is known."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN
from .coordinator import KnvbConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_REFRESH_TOKEN, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KnvbConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an account."""
    teams = {}
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        data = asdict(coordinator.data) if coordinator.data is not None else None
        if data is not None:
            data["durations"] = {str(k): str(v) for k, v in data["durations"].items()}
        teams[subentry_id] = {
            "team": dict(coordinator.subentry.data),
            "update_interval": str(coordinator.update_interval),
            "last_update_success": coordinator.last_update_success,
            "data": data,
        }
    return {"entry": async_redact_data(dict(entry.data), TO_REDACT), "teams": teams}
