"""Calendar with every match of the team."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import (
    ClubData,
    KnvbClubCoordinator,
    KnvbConfigEntry,
    KnvbTeamCoordinator,
    TeamData,
    estimated_duration,
)
from .entity import KnvbClubEntity, KnvbEntity
from .models import Match, MatchStatus

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnvbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a calendar per team."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnvbCalendar(coordinator)], config_subentry_id=subentry_id)
    for subentry_id, club in entry.runtime_data.clubs.items():
        async_add_entities([KnvbClubCalendar(club)], config_subentry_id=subentry_id)


def score_text(match: Match) -> str | None:
    """Return the score as home–away, with extra time and penalties if there were any."""
    if match.score_home is None or match.score_away is None:
        return None
    text = f"{match.score_home}–{match.score_away}"
    if match.extra_time_home is not None and match.extra_time_away is not None:
        text += f", n.v. {match.extra_time_home}–{match.extra_time_away}"
    if match.penalties_home is not None and match.penalties_away is not None:
        text += f", strafschoppen {match.penalties_home}–{match.penalties_away}"
    return text


def dressing_room(data: TeamData, match: Match) -> str | None:
    """Return the team's own dressing room, if assigned."""
    facility = data.facilities.get(match.id)
    if facility is None:
        return None
    if match.is_home(data.team_id):
        return facility.home_dressing_room
    return facility.away_dressing_room


def match_event(data: TeamData, match: Match) -> CalendarEvent | None:
    """Return the calendar event of a match; None if it has no time or was called off.

    Called-off matches are left out, so that automations on the calendar don't fire.
    """
    if match.start is None or match.status is MatchStatus.CANCELLED:
        return None
    summary = f"{match.home.name} – {match.away.name}"
    if match.has_result and (score := score_text(match)):
        summary += f" ({score})"
    facility = data.facilities.get(match.id)
    venue = (facility.name if facility else None) or match.venue
    address = facility.full_address if facility else None
    location = ", ".join(part for part in (venue, address) if part)
    lines = [
        match.pool.label if match.pool else None,
        f"Veld: {facility.pitch}" if facility and facility.pitch else None,
        f"Kleedkamer: {room}" if (room := dressing_room(data, match)) else None,
        f"Verzamelen: {facility.assembly_time}" if facility and facility.assembly_time else None,
        f"Vertrek: {facility.departure_time}" if facility and facility.departure_time else None,
        f"Wedstrijdnummer: {match.number}" if match.number else None,
        match.status_note,
    ]
    description = "\n".join(line for line in lines if line)
    start = dt_util.as_local(match.start)
    return CalendarEvent(
        start=start,
        end=start + data.duration(match),
        summary=summary,
        description=description or None,
        location=location or None,
        uid=f"knvb_voetbal_{match.id}",
    )


class KnvbCalendar(KnvbEntity, CalendarEntity):
    """All matches of a team: past, with scores, and upcoming."""

    _attr_name = None
    _attr_translation_key = "matches"

    def __init__(self, coordinator: KnvbTeamCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        data = self.coordinator.data
        return [event for match in data.matches if (event := match_event(data, match)) is not None]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the match being played now, or else the next one."""
        now = dt_util.now()
        return next((event for event in self._events() if event.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the matches between two moments."""
        return [
            event for event in self._events() if event.start < end_date and event.end > start_date
        ]


def club_event(data: ClubData, match: Match) -> CalendarEvent | None:
    """Return the calendar event of a club match; None if it has no time or was called off."""
    if match.start is None or match.status is MatchStatus.CANCELLED:
        return None
    summary = f"{match.home.name} – {match.away.name}"
    if match.has_result and (score := score_text(match)):
        summary += f" ({score})"
    start = dt_util.as_local(match.start)
    team = data.side(match) or match.home
    return CalendarEvent(
        start=start,
        end=start + estimated_duration(team),
        summary=summary,
        description=match.pool.label if match.pool else None,
        location=match.venue,
        uid=f"knvb_voetbal_{match.id}",
    )


class KnvbClubCalendar(KnvbClubEntity, CalendarEntity):
    """Every match of every team of a club."""

    _attr_name = None
    _attr_translation_key = "club_matches"

    def __init__(self, coordinator: KnvbClubCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, "calendar")

    def _events(self) -> list[CalendarEvent]:
        data = self.coordinator.data
        return [event for match in data.matches if (event := club_event(data, match)) is not None]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the club match being played now, or else the next one."""
        now = dt_util.now()
        return next((event for event in self._events() if event.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the club's matches between two moments."""
        return [
            event for event in self._events() if event.start < end_date and event.end > start_date
        ]
