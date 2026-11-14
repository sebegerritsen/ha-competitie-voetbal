"""The KNVB Voetbal integration: fixtures, results and standings of KNVB teams."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import KnvbClient
from .const import CONF_REFRESH_TOKEN, DOMAIN, SUBENTRY_CLUB, SUBENTRY_TEAM
from .coordinator import (
    KnvbClubCoordinator,
    KnvbConfigEntry,
    KnvbRuntimeData,
    KnvbTeamCoordinator,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KnvbConfigEntry) -> bool:
    """Set up an account and a coordinator per team."""

    @callback
    def _store_refresh_token(token: str) -> None:
        # Refresh tokens are single use: keep the new one at once.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    client = KnvbClient(
        async_get_clientsession(hass),
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        on_refresh_token=_store_refresh_token,
    )
    runtime = KnvbRuntimeData(client)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TEAM:
            runtime.coordinators[subentry_id] = KnvbTeamCoordinator(hass, entry, subentry, client)
        elif subentry.subentry_type == SUBENTRY_CLUB:
            runtime.clubs[subentry_id] = KnvbClubCoordinator(hass, entry, subentry, client)
    await asyncio.gather(
        *(
            coordinator.async_config_entry_first_refresh()
            for coordinator in [*runtime.coordinators.values(), *runtime.clubs.values()]
        )
    )
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    teams = _teams(entry)

    async def _reload_on_team_change(hass: HomeAssistant, entry: KnvbConfigEntry) -> None:
        # Updates also come from storing a new refresh token: only reload when a team
        # was added, removed or reconfigured.
        if _teams(entry) != teams:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_on_team_change))
    return True


def _teams(entry: KnvbConfigEntry) -> dict[str, dict[str, Any]]:
    return {key: dict(subentry.data) for key, subentry in entry.subentries.items()}


async def async_unload_entry(hass: HomeAssistant, entry: KnvbConfigEntry) -> bool:
    """Unload an account."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
