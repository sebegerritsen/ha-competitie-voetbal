"""Event entity: a match was called off, moved, or has a result."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .calendar import score_text
from .changes import ChangeType, MatchChange
from .coordinator import KnvbConfigEntry, KnvbTeamCoordinator, TeamData, match_result
from .entity import KnvbEntity
from .sensor import match_attributes

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnvbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a match-update event entity per team."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnvbMatchUpdate(coordinator)], config_subentry_id=subentry_id)


class KnvbMatchUpdate(KnvbEntity, EventEntity):
    """Fires when one of the team's matches is called off, back on, moved, or has a result."""

    _attr_translation_key = "match_update"

    def __init__(self, coordinator: KnvbTeamCoordinator) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, "match_update")
        self._attr_event_types = [change.value for change in ChangeType]
        # Changes belong to one update: collect each update's once, fire when ready.
        self._seen: TeamData | None = None
        self._pending: list[MatchChange] = []
        self._ready = False

    async def async_added_to_hass(self) -> None:
        """Report what the first update found (what changed while Home Assistant was off).

        Not right away: state written while the entity is being added is dropped, and
        during start-up automations may not be running yet.
        """
        await super().async_added_to_hass()
        self._collect()
        self.async_on_remove(async_call_later(self.hass, 0, self._async_added))

    @callback
    def _async_added(self, _now: datetime) -> None:
        self.async_on_remove(async_at_started(self.hass, self._async_ready))

    @callback
    def _async_ready(self, _hass: HomeAssistant) -> None:
        self._ready = True
        self._fire()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._collect()
        self._fire()
        super()._handle_coordinator_update()

    @callback
    def _collect(self) -> None:
        data = self.coordinator.data
        if data is None or data is self._seen:
            return
        self._seen = data
        self._pending.extend(data.changes)

    @callback
    def _fire(self) -> None:
        if not self._ready:
            return
        while self._pending:
            change = self._pending.pop(0)
            self._trigger_event(change.type.value, self._attributes(change))
            self.async_write_ha_state()

    def _attributes(self, change: MatchChange) -> dict[str, Any]:
        data = self.coordinator.data
        match, before = change.match, change.previous
        attributes = match_attributes(data, match)
        attributes.pop("opponent_logo", None)
        if change.type in (ChangeType.RESCHEDULED, ChangeType.REINSTATED):
            previous = dt_util.parse_datetime(before.start) if before.start else None
            attributes["previous_start"] = (
                dt_util.as_local(previous).isoformat() if previous else None
            )
        if change.type is ChangeType.LOCATION_CHANGED:
            attributes["previous_venue"] = before.venue
            attributes["previous_pitch"] = before.pitch
        if change.type is ChangeType.RESULT:
            attributes["score"] = score_text(match)
            attributes["result"] = match_result(match, data.team_id)
        return attributes
