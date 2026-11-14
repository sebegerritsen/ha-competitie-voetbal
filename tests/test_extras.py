"""Tests for meet time, match today, weather, team settings and match-update events."""

from __future__ import annotations

import copy
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import Event, HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.knvb_voetbal.const import (
    CONF_MEET_MINUTES_AWAY,
    CONF_MEET_MINUTES_HOME,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    SUBENTRY_TEAM,
    UPDATE_INTERVAL,
)

from .conftest import NOW, POOL_CURRENT, TEAM_ID, data_url, load_fixture

UPDATE = "event.vv_voorbeeld_jo13_2_match_update"
TODAY = "binary_sensor.vv_voorbeeld_jo13_2_match_today"
OPPONENT = "sensor.vv_voorbeeld_jo13_2_next_opponent"
HOME_AWAY = "sensor.vv_voorbeeld_jo13_2_home_or_away"
MEET = "sensor.vv_voorbeeld_jo13_2_meet_time"
WEATHER = "sensor.vv_voorbeeld_jo13_2_weather_at_next_match"

pytestmark = pytest.mark.usefixtures("dutch_time")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _events(hass: HomeAssistant) -> list[dict[str, Any]]:
    fired: list[dict[str, Any]] = []

    def _listener(event: Event) -> None:
        new = event.data["new_state"]
        if (
            event.data["entity_id"] == UPDATE
            and new
            and new.state not in ("unknown", "unavailable")
        ):
            fired.append(dict(new.attributes))

    hass.bus.async_listen("state_changed", _listener)
    return fired


def _answer(mock: AiohttpClientMocker, path: str, params: dict[str, str], data: Any) -> None:
    """Replace the answer of one endpoint, keeping the others."""
    mock._mocks = [
        m
        for m in mock._mocks
        if not (
            str(m._url).split("?")[0] == data_url(path)
            and all(m._url.query.get(k) == v for k, v in params.items())
        )
    ]
    mock.get(data_url(path), params=params, json=data)


async def test_next_match_sensors_and_match_today(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Opponent, home or away, meet time and match today."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    state = hass.states.get(OPPONENT)
    assert state.state == "SV Fictief JO13-1"
    assert state.attributes["club"] == "SV Fictief"
    assert state.attributes["opponent_points"] == 3
    assert hass.states.get(HOME_AWAY).state == "home"
    assert hass.states.get(MEET).state == "2026-11-14T08:15:00+00:00"
    state = hass.states.get(TODAY)
    assert state.state == "on"
    assert state.attributes["dressing_room"] == "3"
    assert state.attributes["opponent"] == "SV Fictief JO13-1"
    assert hass.states.get(WEATHER) is None  # only with a weather entity

    # After the match, the next one is away next Saturday; Sunday has no match.
    freezer.move_to("2026-11-15T09:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(TODAY).state == "off"
    assert hass.states.get(HOME_AWAY).state == "away"


async def test_meet_time_from_settings(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Without a departure time from the club, kick-off minus the set minutes."""
    facility = load_fixture("facility.json") | {"DepartureTime": None}
    _answer(mock_api, "match/MatchFacility", {"v": "2", "PublicMatchId": "M100"}, facility)
    subentry = next(iter(config_entry.subentries.values()))
    freezer.move_to(NOW)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_MEET_MINUTES_HOME: 45}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(MEET)
    assert state.state == "2026-11-14T08:15:00+00:00"  # 10:00 local minus 45 min
    assert state.attributes["meet_time_source"] == "settings"


async def test_settings_and_weather(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Reconfigure a team; a weather entity adds the forecast at kick-off."""

    async def _forecasts(call: ServiceCall) -> dict[str, Any]:
        if call.data["type"] != "hourly":
            return {}
        return {
            call.data["entity_id"]: {
                "forecast": [
                    {"datetime": "2026-11-14T08:00:00+00:00", "temperature": 6.0},
                    {
                        "datetime": "2026-11-14T09:00:00+00:00",
                        "temperature": 7.4,
                        "condition": "rainy",
                        "precipitation_probability": 60,
                        "wind_speed": 18.0,
                    },
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", _forecasts, supports_response=SupportsResponse.ONLY
    )
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    subentry_id = next(iter(config_entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TEAM),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_MEET_MINUTES_HOME: 30,
            CONF_MEET_MINUTES_AWAY: 60,
            CONF_WEATHER_ENTITY: "weather.home",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    data = config_entry.subentries[subentry_id].data
    assert data[CONF_MEET_MINUTES_AWAY] == 60
    assert data[CONF_WEATHER_ENTITY] == "weather.home"

    state = hass.states.get(WEATHER)
    assert float(state.state) == pytest.approx(7.4)
    assert state.attributes["condition"] == "rainy"
    assert state.attributes["precipitation_probability"] == 60
    assert state.attributes["match_id"] == "M100"


async def test_match_updates(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A result, a move to another time and a called-off match fire events."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    fired = _events(hass)

    program = load_fixture("team_program.json")
    later = copy.deepcopy(program)
    del later["ProgramItemMatch"][0]  # played: now in the results
    moved = later["ProgramItemMatch"][0]["Match"]
    moved["MatchDateTime"] = "2026-11-21T11:00:00+0100"
    _answer(mock_api, "team/TeamProgram", {"v": "3", "PublicTeamId": TEAM_ID}, later)
    pool = load_fixture("pool_current.json")
    played = copy.deepcopy(pool["MatchResult"][0])
    played |= {"PublicMatchId": "M100", "MatchDateTime": "2026-11-14T10:00:00+0100"}
    played["HomeTeam"], played["AwayTeam"] = (
        program["ProgramItemMatch"][0]["Match"]["HomeTeam"],
        program["ProgramItemMatch"][0]["Match"]["AwayTeam"],
    )
    played["HomeResult"]["Score"], played["AwayResult"]["Score"] = 1, 1
    pool["MatchResult"].append(played)
    _answer(mock_api, "pool/PoolCompetitionData", {"v": "2", "PoolId": str(POOL_CURRENT)}, pool)

    freezer.move_to("2026-11-14T12:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [(e["event_type"], e["match_id"]) for e in fired] == [
        ("result", "M100"),
        ("rescheduled", "M101"),
    ]
    assert fired[0]["score"] == "1–1"
    assert fired[0]["result"] == "draw"
    assert fired[1]["previous_start"] == "2026-11-21T08:30:00+01:00"

    called_off = copy.deepcopy(later)
    called_off["ProgramItemMatch"][0]["Match"] |= {
        "DisciplineStatus": "ADO",
        "DisciplineStatusDescription": "Afgelast door vereniging",
    }
    _answer(mock_api, "team/TeamProgram", {"v": "3", "PublicTeamId": TEAM_ID}, called_off)
    freezer.tick(UPDATE_INTERVAL * 7)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert fired[-1]["event_type"] == "cancelled"
    assert fired[-1]["status_note"] == "Afgelast door vereniging"


async def test_change_while_home_assistant_was_off(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Changes since the matches were last seen are reported after a restart."""
    subentry_id = next(iter(config_entry.subentries))
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}.{subentry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}.{subentry_id}",
        "data": {
            "matches": {
                "M101": {
                    "status": "scheduled",
                    "start": "2026-11-21T09:30:00+01:00",
                    "venue": "Sportpark Denkbeeld",
                    "pitch": None,
                    "score": None,
                }
            }
        },
    }
    freezer.move_to(NOW)
    fired = _events(hass)
    await _setup(hass, config_entry)
    async_fire_time_changed(hass)  # the entity reports once it has been added
    await hass.async_block_till_done()
    assert [e["event_type"] for e in fired] == ["rescheduled"]
    assert fired[0]["previous_start"] == "2026-11-21T09:30:00+01:00"


def test_daily_forecast_matches_the_date() -> None:
    """A daily forecast (stamped at midnight) is picked by the match's date, not nearness."""
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from custom_components.knvb_voetbal.coordinator import _forecast_item  # noqa: PLC0415

    items = [
        {"datetime": "2026-11-14T00:00:00+01:00", "temperature": 9.0},
        {"datetime": "2026-11-15T00:00:00+01:00", "temperature": 10.0},
    ]
    kickoff = datetime(2026, 11, 14, 15, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert _forecast_item(items, kickoff, hourly=False)["temperature"] == 9.0
    assert _forecast_item(items, kickoff, hourly=True) is None
