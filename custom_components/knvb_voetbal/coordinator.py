"""Coordinator: one team's matches, results, standing and venue details."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import KnvbAuthError, KnvbClient, KnvbConnectionError, KnvbError
from .changes import MatchChange, MatchState, detect_changes, updated_states
from .const import (
    CONF_MEET_MINUTES_AWAY,
    CONF_MEET_MINUTES_HOME,
    CONF_TEAM_ID,
    CONF_WEATHER_ENTITY,
    DEFAULT_MATCH_DURATION,
    DEFAULT_MEET_MINUTES,
    DOMAIN,
    FACILITY_DAYS_AHEAD,
    FACILITY_REFRESH,
    FINISHED_POOL_REFRESH,
    HALF_TIME,
    LOGGER,
    MATCH_LEAD,
    MAX_MEET_LEAD,
    PLAYING_TIME_REFRESH,
    RESULT_WAIT,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_IDLE,
    UPDATE_INTERVAL_MATCH,
    UPDATE_INTERVAL_MIN,
    WEATHER_AHEAD,
    WEATHER_REFRESH,
)
from .models import NL_TZ, Facility, Match, MatchStatus, Pool, PoolData, StandingRow

NOT_PLAYED = frozenset({MatchStatus.CANCELLED})
DONE = frozenset({MatchStatus.FINAL, MatchStatus.CANCELLED, MatchStatus.ABANDONED})


@dataclass(slots=True)
class KnvbRuntimeData:
    """What a loaded account holds: its client and a coordinator per team subentry."""

    client: KnvbClient
    coordinators: dict[str, KnvbTeamCoordinator] = field(default_factory=dict)


type KnvbConfigEntry = ConfigEntry[KnvbRuntimeData]


def match_duration(net: int | None, gross: int | None) -> timedelta:
    """Return the calendar slot: playing time plus half-time, within the booked slot."""
    if not net:
        return timedelta(minutes=gross) if gross else DEFAULT_MATCH_DURATION
    duration = timedelta(minutes=net) + HALF_TIME
    if gross:
        duration = min(duration, timedelta(minutes=gross))
    return duration


def match_result(match: Match, team_id: str) -> str | None:
    """Return win, draw or loss for a team (after extra time; penalties keep a draw)."""
    if (goals := match.goals(team_id)) is None:
        return None
    goals_for, goals_against = goals
    if goals_for > goals_against:
        return "win"
    if goals_for < goals_against:
        return "loss"
    return "draw"


@dataclass(frozen=True, slots=True)
class MatchWeather:
    """The forecast at kick-off, from the weather entity chosen for the team."""

    condition: str | None
    temperature: float | None
    apparent_temperature: float | None
    precipitation_probability: float | None
    precipitation: float | None
    wind_speed: float | None
    humidity: float | None
    forecast_time: str | None

    @classmethod
    def from_forecast(cls, item: dict[str, Any]) -> MatchWeather:
        """Parse one forecast item of weather.get_forecasts."""

        def number(key: str) -> float | None:
            value = item.get(key)
            return float(value) if isinstance(value, int | float) else None

        condition = item.get("condition")
        moment = item.get("datetime")
        return cls(
            condition=condition if isinstance(condition, str) else None,
            temperature=number("temperature"),
            apparent_temperature=number("apparent_temperature"),
            precipitation_probability=number("precipitation_probability"),
            precipitation=number("precipitation"),
            wind_speed=number("wind_speed"),
            humidity=number("humidity"),
            forecast_time=moment if isinstance(moment, str) else None,
        )


@dataclass(frozen=True, slots=True)
class SeasonRecord:
    """The team's results over the whole season, all pools and friendlies together."""

    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int


@dataclass(slots=True)
class TeamData:
    """Everything known about one team."""

    team_id: str
    matches: list[Match]
    standings: list[StandingRow] = field(default_factory=list)
    pool: Pool | None = None
    facilities: dict[str, Facility] = field(default_factory=dict)
    durations: dict[int, timedelta] = field(default_factory=dict)
    # Every result of the current pool, for the form of the other teams.
    pool_results: list[Match] = field(default_factory=list)
    meet_minutes_home: int = DEFAULT_MEET_MINUTES
    meet_minutes_away: int = DEFAULT_MEET_MINUTES
    # Forecast for the next match (`weather_match_id`), if a weather entity is set.
    weather: MatchWeather | None = None
    weather_match_id: str | None = None
    # What changed since the previous update (or since Home Assistant last ran).
    changes: list[MatchChange] = field(default_factory=list)

    @property
    def standing(self) -> StandingRow | None:
        """Return the team's own row in the current pool's standing."""
        return self.standing_of(self.team_id)

    def standing_of(self, team_id: str) -> StandingRow | None:
        """Return a team's row in the current pool's standing."""
        return next((row for row in self.standings if row.team.id == team_id), None)

    def points_to_leader(self) -> int | None:
        """Return how many points the team is behind the leader (0 when leading)."""
        own = self.standing
        if own is None or own.points is None or not self.standings:
            return None
        top = max((row.points for row in self.standings if row.points is not None), default=None)
        return None if top is None else top - own.points

    def upcoming(self, now: datetime) -> list[Match]:
        """Return the match being played now and those after it (not called off)."""
        return [
            match
            for match in self.matches
            if match.start is not None
            and match.status not in DONE
            and match.start + self.duration(match) > now
        ]

    def matches_on(self, day: date) -> list[Match]:
        """Return the team's matches (not called off) on a day, local time."""
        return [
            match
            for match in self.matches
            if match.start is not None
            and match.status is not MatchStatus.CANCELLED
            and dt_util.as_local(match.start).date() == day
        ]

    def form(self, team_id: str, count: int = 5) -> list[str]:
        """Return a team's last results, oldest first, as win, draw or loss."""
        source = self.matches if team_id == self.team_id else self.pool_results
        results = [
            result
            for match in source
            if match.involves(team_id)
            and match.has_result
            and (result := match_result(match, team_id)) is not None
        ]
        return results[-count:]

    def previous_meeting(self, match: Match) -> Match | None:
        """Return the last earlier result against the same opponent."""
        opponent = match.opponent(self.team_id).id
        if match.start is None:
            return None
        return next(
            (
                earlier
                for earlier in reversed(self.matches)
                if earlier.id != match.id
                and earlier.start is not None
                and earlier.start < match.start
                and earlier.opponent(self.team_id).id == opponent
                and earlier.has_result
            ),
            None,
        )

    def season_record(self) -> SeasonRecord | None:
        """Return the team's results over the whole season."""
        played = [match for match in self.matches if match.has_result]
        if not played:
            return None
        results = [match_result(match, self.team_id) for match in played]
        goals = [goal for match in played if (goal := match.goals(self.team_id))]
        return SeasonRecord(
            played=len(played),
            won=results.count("win"),
            drawn=results.count("draw"),
            lost=results.count("loss"),
            goals_for=sum(g[0] for g in goals),
            goals_against=sum(g[1] for g in goals),
        )

    def meet_time(self, match: Match | None) -> tuple[datetime | None, str | None]:
        """Return when the team meets for a match, and where that time comes from.

        The club's departure time (vertrektijd) when it entered one, else kick-off
        minus the minutes set for home or away matches.
        """
        if match is None or match.start is None:
            return None, None
        facility = self.facilities.get(match.id)
        if facility is not None and facility.departure_time:
            try:
                clock = time.fromisoformat(facility.departure_time)
            except ValueError:
                clock = None
            if clock is not None:
                day = match.start.astimezone(NL_TZ).date()
                meet = datetime.combine(day, clock, NL_TZ)
                if match.start - MAX_MEET_LEAD <= meet <= match.start:
                    return meet, "club"
        minutes = self.meet_minutes_home if match.is_home(self.team_id) else self.meet_minutes_away
        return match.start - timedelta(minutes=minutes), "settings"

    def duration(self, match: Match) -> timedelta:
        """Return how long a match is expected to take."""
        if match.pool is not None and match.pool.id in self.durations:
            return self.durations[match.pool.id]
        return DEFAULT_MATCH_DURATION

    def match_end(self, match: Match) -> datetime | None:
        """Return when a match is expected to end."""
        return match.start + self.duration(match) if match.start else None

    def next_match(self, now: datetime) -> Match | None:
        """Return the match being played now, or else the next one."""
        for match in self.matches:
            if match.start is None or match.status in DONE:
                continue
            if match.start + self.duration(match) > now:
                return match
        return None

    def last_result(self) -> Match | None:
        """Return the most recent match with a result."""
        return next((match for match in reversed(self.matches) if match.has_result), None)


def _sort_key(match: Match) -> tuple[bool, datetime]:
    return (match.start is None, match.start or datetime.max.replace(tzinfo=dt_util.UTC))


class KnvbTeamCoordinator(DataUpdateCoordinator[TeamData]):
    """Fetches a team's programme, its pools' results and standing, and venue details."""

    config_entry: KnvbConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: KnvbConfigEntry,
        subentry: ConfigSubentry,
        client: KnvbClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.subentry = subentry
        self.team_id: str = subentry.data[CONF_TEAM_ID]
        self._pools: dict[int, tuple[datetime, PoolData]] = {}
        self._facilities: dict[str, tuple[datetime, Facility]] = {}
        self._durations: dict[int, tuple[datetime, timedelta | None]] = {}
        # Venue names from the programme, kept once a match moves to the results.
        self._venues: dict[str, str] = {}
        self._weather: tuple[str, datetime, MatchWeather | None] | None = None
        # The matches as last seen, to notice changes; kept across restarts.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{subentry.subentry_id}"
        )
        self._states: dict[str, MatchState] = {}

    async def _async_setup(self) -> None:
        """Load the matches as they were before Home Assistant stopped."""
        stored = await self._store.async_load()
        matches = stored.get("matches") if isinstance(stored, dict) else None
        if isinstance(matches, dict):
            for key, value in matches.items():
                if (state := MatchState.from_dict(value)) is not None:
                    self._states[str(key)] = state

    async def _async_update_data(self) -> TeamData:
        try:
            data = await self._fetch(dt_util.utcnow())
        except KnvbAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except KnvbConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except KnvbError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": str(err)},
            ) from err
        self.update_interval = self._next_interval(data, dt_util.utcnow())
        return data

    async def _fetch(self, now: datetime) -> TeamData:
        program = [
            match
            for match in await self.client.get_team_program(self.team_id)
            if match.involves(self.team_id)
        ]
        competition = await self.client.get_team_competition(self.team_id)
        current = competition.current_pool()

        matches: dict[str, Match] = {}
        for match in program:
            matches[match.id] = match
            if match.venue:
                self._venues[match.id] = match.venue
        results = list(competition.other_results)
        for pool in competition.pools:
            results.extend((await self._pool(pool, now, current=pool == current)).results)
        for match in results:
            if match.involves(self.team_id):
                known = matches.get(match.id)
                matches[match.id] = known.merge(match) if known else match
        for match_id, match in list(matches.items()):
            if match.venue is None and (venue := self._venues.get(match_id)):
                matches[match_id] = replace(match, venue=venue)

        ordered = sorted(matches.values(), key=_sort_key)
        await self._fetch_durations(ordered, now)
        await self._fetch_facilities(ordered, now)
        current_data = self._pools[current.id][1] if current and current.id in self._pools else None
        settings = self.subentry.data
        data = TeamData(
            team_id=self.team_id,
            matches=ordered,
            standings=current_data.standings if current_data else [],
            pool=current,
            facilities={match_id: facility for match_id, (_, facility) in self._facilities.items()},
            durations={pid: d for pid, (_, d) in self._durations.items() if d is not None},
            pool_results=sorted(current_data.results, key=_sort_key) if current_data else [],
            meet_minutes_home=int(settings.get(CONF_MEET_MINUTES_HOME, DEFAULT_MEET_MINUTES)),
            meet_minutes_away=int(settings.get(CONF_MEET_MINUTES_AWAY, DEFAULT_MEET_MINUTES)),
        )
        await self._update_weather(data, now)
        await self._track_changes(data, now)
        return data

    async def _track_changes(self, data: TeamData, now: datetime) -> None:
        """Compare with the matches as last seen; remember the new state."""
        current = []
        for match in data.matches:
            facility = data.facilities.get(match.id)
            current.append((match, MatchState.of(match, facility.pitch if facility else None)))
        data.changes = detect_changes(self._states, current)
        states = updated_states(self._states, current, now)
        if states != self._states:
            self._states = states
            await self._store.async_save(
                {"matches": {key: value.as_dict() for key, value in states.items()}}
            )

    async def _update_weather(self, data: TeamData, now: datetime) -> None:
        """Look up the forecast at the next kick-off, from 48 hours before."""
        entity_id = self.subentry.data.get(CONF_WEATHER_ENTITY)
        match = data.next_match(now)
        if not entity_id or match is None or match.start is None:
            self._weather = None
            return
        if match.start - now > WEATHER_AHEAD:
            self._weather = None
            return
        cached = self._weather if self._weather and self._weather[0] == match.id else None
        if cached is None or now - cached[1] >= WEATHER_REFRESH:
            forecast = await self._forecast(entity_id, match.start)
            self._weather = (match.id, now, forecast or (cached[2] if cached else None))
        data.weather_match_id, _fetched, data.weather = self._weather

    async def _forecast(self, entity_id: str, start: datetime) -> MatchWeather | None:
        """Return the forecast at kick-off: the nearest hour, else the day of the match."""
        for kind in ("hourly", "daily"):
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": entity_id, "type": kind},
                    blocking=True,
                    return_response=True,
                )
            except (HomeAssistantError, ValueError) as err:
                LOGGER.debug("No %s forecast from %s: %s", kind, entity_id, err)
                continue
            items = (response or {}).get(entity_id, {}).get("forecast") or []
            if item := _forecast_item(items, start, hourly=kind == "hourly"):
                return MatchWeather.from_forecast(item)
        return None

    async def _pool(self, pool: Pool, now: datetime, *, current: bool) -> PoolData:
        """Return a pool's data: the current pool every time, finished ones twice a day."""
        cached = self._pools.get(pool.id)
        finished = pool.next_match is None and not current
        if cached and finished and now - cached[0] < FINISHED_POOL_REFRESH:
            return cached[1]
        try:
            data = await self.client.get_pool(pool.id)
        except KnvbAuthError:
            raise
        except KnvbError as err:
            if current or (cached is None and not finished):
                raise
            LOGGER.debug("Could not fetch pool %s: %s", pool.id, err)
            return cached[1] if cached else PoolData()
        self._pools[pool.id] = (now, data)
        return data

    async def _fetch_durations(self, matches: list[Match], now: datetime) -> None:
        """Look up each pool's playing time once a day, from one of its matches."""
        by_pool: dict[int, Match] = {}
        for match in matches:
            if match.pool is not None:
                by_pool.setdefault(match.pool.id, match)
        for pool_id, match in by_pool.items():
            cached = self._durations.get(pool_id)
            if cached and now - cached[0] < PLAYING_TIME_REFRESH:
                continue
            try:
                net, gross = await self.client.get_playing_time(match.id)
            except KnvbAuthError:
                raise
            except KnvbError as err:
                LOGGER.debug("No playing time for pool %s: %s", pool_id, err)
                self._durations[pool_id] = (now, cached[1] if cached else None)
                continue
            self._durations[pool_id] = (now, match_duration(net, gross))

    async def _fetch_facilities(self, matches: list[Match], now: datetime) -> None:
        """Fetch venue details of the matches in the coming week."""
        upcoming = {
            match.id
            for match in matches
            if match.start is not None
            and match.status not in DONE
            and now - RESULT_WAIT <= match.start <= now + FACILITY_DAYS_AHEAD
        }
        for match_id in list(self._facilities):
            if match_id not in upcoming and not any(m.id == match_id for m in matches):
                del self._facilities[match_id]
        for match_id in sorted(upcoming):
            cached = self._facilities.get(match_id)
            if cached and now - cached[0] < FACILITY_REFRESH:
                continue
            try:
                facility = await self.client.get_facility(match_id)
            except KnvbAuthError:
                raise
            except KnvbError as err:
                LOGGER.debug("No venue details for match %s: %s", match_id, err)
                if cached is None:
                    continue
                facility = cached[1]
            self._facilities[match_id] = (now, facility)

    def _next_interval(self, data: TeamData, now: datetime) -> timedelta:
        """Poll more often around the team's own matches, less often without any."""
        if any(self._around(data, match, now) for match in data.matches):
            return UPDATE_INTERVAL_MATCH
        upcoming = [
            match.start
            for match in data.matches
            if match.start and match.start > now and match.status not in DONE
        ]
        if not upcoming:
            return UPDATE_INTERVAL_IDLE
        until_lead = min(upcoming) - MATCH_LEAD - now
        if until_lead < UPDATE_INTERVAL:
            return max(until_lead, UPDATE_INTERVAL_MIN)
        return UPDATE_INTERVAL

    @staticmethod
    def _around(data: TeamData, match: Match, now: datetime) -> bool:
        """Return whether a match is about to start, playing, or waiting for its result."""
        if match.start is None or match.status in DONE:
            return False
        return match.start - MATCH_LEAD <= now <= match.start + RESULT_WAIT


def _forecast_item(
    items: list[dict[str, Any]], start: datetime, *, hourly: bool
) -> dict[str, Any] | None:
    """Return the hourly item within 90 minutes of kick-off, or the daily one of its date.

    Daily items are stamped at midnight (local), so they are matched on the date.
    """
    best: tuple[timedelta, dict[str, Any]] | None = None
    match_day = dt_util.as_local(start).date()
    for item in items:
        moment = dt_util.parse_datetime(str(item.get("datetime", "")))
        if moment is None:
            continue
        if not hourly:
            if dt_util.as_local(moment).date() == match_day:
                return item
            continue
        gap = abs(moment - start)
        if gap <= timedelta(minutes=90) and (best is None or gap < best[0]):
            best = (gap, item)
    return best[1] if best else None
