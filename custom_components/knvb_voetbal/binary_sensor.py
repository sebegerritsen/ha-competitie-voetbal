"""Binary sensor: does the team play today?"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .calendar import dressing_room
from .coordinator import KnvbConfigEntry, KnvbTeamCoordinator
from .entity import KnvbEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnvbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a match-today sensor per team."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnvbMatchToday(coordinator)], config_subentry_id=subentry_id)


class KnvbMatchToday(KnvbEntity, BinarySensorEntity):
    """On all day when the team has a match (not called off) today."""

    _attr_translation_key = "match_today"

    def __init__(self, coordinator: KnvbTeamCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "match_today")

    async def async_added_to_hass(self) -> None:
        """Also update just after midnight, when the day changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._new_day, hour=0, minute=0, second=1)
        )

    @callback
    def _new_day(self, now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return whether the team plays today."""
        return bool(self.coordinator.data.matches_on(dt_util.now().date()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return today's first match."""
        data = self.coordinator.data
        today = data.matches_on(dt_util.now().date())
        if not today:
            return {}
        match = today[0]
        meet, _source = data.meet_time(match)
        facility = data.facilities.get(match.id)
        return {
            "start": dt_util.as_local(match.start).isoformat() if match.start else None,
            "meet_time": dt_util.as_local(meet).isoformat() if meet else None,
            "opponent": match.opponent(data.team_id).name,
            "home_away": "home" if match.is_home(data.team_id) else "away",
            "venue": (facility.name if facility else None) or match.venue,
            "pitch": facility.pitch if facility else None,
            "dressing_room": dressing_room(data, match),
            "matches": len(today),
        }
