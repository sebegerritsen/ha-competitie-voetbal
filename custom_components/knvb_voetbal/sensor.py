"""Sensors: next match, opponent, home or away, meet time, weather, last result, standing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .calendar import dressing_room, score_text
from .const import CONF_WEATHER_ENTITY
from .coordinator import KnvbConfigEntry, KnvbTeamCoordinator, TeamData, match_result
from .entity import KnvbEntity
from .models import Match

PARALLEL_UPDATES = 0

RESULT_WIN = "win"
RESULT_DRAW = "draw"
RESULT_LOSS = "loss"
HOME = "home"
AWAY = "away"
# Upcoming matches listed with the next match.
UPCOMING_COUNT = 5


def _iso(moment: datetime | None) -> str | None:
    return dt_util.as_local(moment).isoformat() if moment else None


def match_attributes(data: TeamData, match: Match | None) -> dict[str, Any]:
    """Return the attributes describing a match from the team's point of view."""
    if match is None:
        return {}
    facility = data.facilities.get(match.id)
    opponent = match.opponent(data.team_id)
    meet, meet_source = data.meet_time(match)
    return {
        "opponent": opponent.name,
        "home_away": HOME if match.is_home(data.team_id) else AWAY,
        "home_team": match.home.name,
        "away_team": match.away.name,
        "start": _iso(match.start),
        "end": _iso(data.match_end(match)),
        "meet_time": _iso(meet),
        "meet_time_source": meet_source,
        "venue": (facility.name if facility else None) or match.venue,
        "address": facility.full_address if facility else None,
        "pitch": facility.pitch if facility else None,
        "dressing_room": dressing_room(data, match),
        "competition": match.pool.label if match.pool else None,
        "cup": match.pool.is_cup if match.pool else False,
        "status": match.status.value,
        "status_note": match.status_note,
        "match_id": match.id,
        "opponent_logo": opponent.logo,
    }


def opponent_details(data: TeamData, match: Match) -> dict[str, Any]:
    """Return how the opponent is doing in the current pool."""
    opponent = match.opponent(data.team_id)
    row = data.standing_of(opponent.id)
    return {
        "opponent_position": row.position if row else None,
        "opponent_points": row.points if row else None,
        "opponent_form": data.form(opponent.id),
    }


def next_match_attributes(data: TeamData, now: datetime) -> dict[str, Any]:
    """Return the next match, what is known of the opponent, and the matches after it."""
    upcoming = data.upcoming(now)
    if not upcoming:
        return {}
    match = upcoming[0]
    previous = data.previous_meeting(match)
    return {
        **match_attributes(data, match),
        **opponent_details(data, match),
        "previous_meeting": score_text(previous) if previous else None,
        "previous_meeting_result": match_result(previous, data.team_id) if previous else None,
        "upcoming": [
            {
                "start": _iso(later.start),
                "opponent": later.opponent(data.team_id).name,
                "home_away": HOME if later.is_home(data.team_id) else AWAY,
                "venue": later.venue,
            }
            for later in upcoming[1 : 1 + UPCOMING_COUNT]
        ],
    }


def opponent_attributes(data: TeamData, now: datetime) -> dict[str, Any]:
    """Return how the next opponent is doing, and where and when the match is."""
    if (match := data.next_match(now)) is None:
        return {}
    facility = data.facilities.get(match.id)
    return {
        "home_away": HOME if match.is_home(data.team_id) else AWAY,
        "start": _iso(match.start),
        "venue": (facility.name if facility else None) or match.venue,
        "club": match.opponent(data.team_id).club_name,
        **opponent_details(data, match),
    }


def result_attributes(data: TeamData, match: Match | None) -> dict[str, Any]:
    """Return the attributes of the last result."""
    if match is None:
        return {}
    goals = match.goals(data.team_id)
    attributes = match_attributes(data, match)
    for key in ("meet_time", "meet_time_source", "pitch", "dressing_room"):
        attributes.pop(key)
    return {
        **attributes,
        "score": score_text(match),
        "goals_for": goals[0] if goals else None,
        "goals_against": goals[1] if goals else None,
        "form": data.form(data.team_id),
    }


def standing_attributes(data: TeamData) -> dict[str, Any]:
    """Return the team's numbers in the standing, its form, the season, and the table."""
    form = data.form(data.team_id)
    season = data.season_record()
    season_attributes = (
        {
            "season_played": season.played,
            "season_won": season.won,
            "season_drawn": season.drawn,
            "season_lost": season.lost,
            "season_goals_for": season.goals_for,
            "season_goals_against": season.goals_against,
        }
        if season
        else {}
    )
    if (row := data.standing) is None:
        return {"form": form, **season_attributes} if form or season else {}
    return {
        "points": row.points,
        "played": row.played,
        "won": row.won,
        "drawn": row.drawn,
        "lost": row.lost,
        "goals_for": row.goals_for,
        "goals_against": row.goals_against,
        "goal_difference": row.goal_difference,
        "penalty_points": row.penalty_points,
        "points_to_leader": data.points_to_leader(),
        "form": form,
        "teams": len(data.standings),
        "competition": data.pool.label if data.pool else None,
        **season_attributes,
        "table": [
            {
                "position": other.position,
                "team": other.team.name,
                "played": other.played,
                "points": other.points,
                "goal_difference": other.goal_difference,
            }
            for other in data.standings
        ],
    }


def _weather_ready(data: TeamData, now: datetime) -> bool:
    match = data.next_match(now)
    return data.weather is not None and match is not None and data.weather_match_id == match.id


def weather_attributes(data: TeamData, now: datetime) -> dict[str, Any]:
    """Return the forecast details for the next match."""
    if not _weather_ready(data, now):
        return {}
    weather, match = data.weather, data.next_match(now)
    assert weather is not None and match is not None
    return {
        "condition": weather.condition,
        "apparent_temperature": weather.apparent_temperature,
        "precipitation_probability": weather.precipitation_probability,
        "precipitation": weather.precipitation,
        "wind_speed": weather.wind_speed,
        "humidity": weather.humidity,
        "forecast_time": weather.forecast_time,
        "match_id": match.id,
        "start": _iso(match.start),
    }


def _weather_value(data: TeamData, now: datetime) -> float | None:
    return data.weather.temperature if _weather_ready(data, now) and data.weather else None


def _result_value(data: TeamData, now: datetime) -> str | None:
    match = data.last_result()
    return match_result(match, data.team_id) if match else None


def _opponent_value(data: TeamData, now: datetime) -> str | None:
    match = data.next_match(now)
    return match.opponent(data.team_id).name if match else None


def _home_away_value(data: TeamData, now: datetime) -> str | None:
    match = data.next_match(now)
    if match is None:
        return None
    return HOME if match.is_home(data.team_id) else AWAY


def _opponent_logo(data: TeamData, now: datetime) -> str | None:
    match = data.next_match(now)
    return match.opponent(data.team_id).logo if match else None


@dataclass(frozen=True, kw_only=True)
class KnvbSensorDescription(SensorEntityDescription):
    """Describes a team sensor."""

    value_fn: Callable[[TeamData, datetime], StateType | datetime]
    attributes_fn: Callable[[TeamData, datetime], dict[str, Any]] = lambda data, now: {}
    picture_fn: Callable[[TeamData, datetime], str | None] | None = None


SENSORS: tuple[KnvbSensorDescription, ...] = (
    KnvbSensorDescription(
        key="next_match",
        translation_key="next_match",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: (match := data.next_match(now)) and match.start,
        attributes_fn=next_match_attributes,
        picture_fn=_opponent_logo,
    ),
    KnvbSensorDescription(
        key="opponent",
        translation_key="opponent",
        value_fn=_opponent_value,
        attributes_fn=opponent_attributes,
        picture_fn=_opponent_logo,
    ),
    KnvbSensorDescription(
        key="home_away",
        translation_key="home_away",
        device_class=SensorDeviceClass.ENUM,
        options=[HOME, AWAY],
        value_fn=_home_away_value,
        attributes_fn=lambda data, now: match_attributes(data, data.next_match(now)),
    ),
    KnvbSensorDescription(
        key="meet_time",
        translation_key="meet_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: data.meet_time(data.next_match(now))[0],
        attributes_fn=lambda data, now: match_attributes(data, data.next_match(now)),
    ),
    KnvbSensorDescription(
        key="last_result",
        translation_key="last_result",
        device_class=SensorDeviceClass.ENUM,
        options=[RESULT_WIN, RESULT_DRAW, RESULT_LOSS],
        value_fn=_result_value,
        attributes_fn=lambda data, now: result_attributes(data, data.last_result()),
    ),
    KnvbSensorDescription(
        key="standing",
        translation_key="standing",
        value_fn=lambda data, now: (row := data.standing) and row.position,
        attributes_fn=lambda data, now: standing_attributes(data),
    ),
)

WEATHER_SENSOR = KnvbSensorDescription(
    key="match_weather",
    translation_key="match_weather",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    suggested_display_precision=0,
    value_fn=_weather_value,
    attributes_fn=weather_attributes,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnvbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of each team (weather only when a weather entity is set)."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        descriptions = list(SENSORS)
        if coordinator.subentry.data.get(CONF_WEATHER_ENTITY):
            descriptions.append(WEATHER_SENSOR)
        async_add_entities(
            (KnvbSensor(coordinator, description) for description in descriptions),
            config_subentry_id=subentry_id,
        )


class KnvbSensor(KnvbEntity, SensorEntity):
    """A sensor of a team."""

    entity_description: KnvbSensorDescription
    _unrecorded_attributes = frozenset(
        {"table", "upcoming", "opponent_logo", "opponent_form", "form", "status_note"}
    )

    def __init__(
        self, coordinator: KnvbTeamCoordinator, description: KnvbSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details."""
        return self.entity_description.attributes_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def entity_picture(self) -> str | None:
        """Return a picture: the opponent's club logo for the next match."""
        if self.entity_description.picture_fn is None:
            return None
        return self.entity_description.picture_fn(self.coordinator.data, dt_util.utcnow())
