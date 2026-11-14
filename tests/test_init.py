"""Tests for setting up an account: coordinators, sensors, calendar, tokens, diagnostics."""

from __future__ import annotations

from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.competitie_voetbal.api import TOKEN_URL
from custom_components.competitie_voetbal.const import (
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_TEAM,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_IDLE,
    UPDATE_INTERVAL_MATCH,
)
from custom_components.competitie_voetbal.diagnostics import async_get_config_entry_diagnostics

from .conftest import NOW, TEAM_DATA, calls_to, data_url, load_fixture

CALENDAR = "calendar.vv_voorbeeld_jo13_2"
NEXT_MATCH = "sensor.vv_voorbeeld_jo13_2_next_match"
LAST_RESULT = "sensor.vv_voorbeeld_jo13_2_last_result"
STANDING = "sensor.vv_voorbeeld_jo13_2_standing"

pytestmark = pytest.mark.usefixtures("dutch_time")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_entities(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A team gets a calendar and three sensors; the new refresh token is stored."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-1"

    state = hass.states.get(NEXT_MATCH)
    assert state.state == "2026-11-14T09:00:00+00:00"
    attrs = state.attributes
    assert attrs["opponent"] == "SV Fictief JO13-1"
    assert attrs["home_away"] == "home"
    assert attrs["address"] == "Voorbeeldlaan 1, 1234 AB Voordorp"
    assert attrs["pitch"] == "veld 2"
    assert attrs["dressing_room"] == "3"
    assert attrs["meet_time"] == "2026-11-14T09:15:00+01:00"  # the club's departure time
    assert attrs["meet_time_source"] == "club"
    assert attrs["opponent_logo"] == "https://logoapi.voetbal.nl/logo.php?clubcode=BBXX02B"
    assert attrs["opponent_position"] == 1
    assert attrs["opponent_form"] == ["draw"]
    assert attrs["upcoming"][0]["opponent"] == "RKVV Denkbeeld JO13-3"
    assert attrs["cup"] is False
    assert state.attributes["entity_picture"].endswith("BBXX02B")
    assert attrs["end"] == "2026-11-14T11:15:00+01:00"  # 60 min play + half-time
    assert attrs["competition"] == "3e klasse (2e fase) – 24"

    state = hass.states.get(LAST_RESULT)
    assert state.state == "win"
    assert state.attributes["score"] == "3–1"
    assert state.attributes["opponent"] == "RKVV Denkbeeld JO13-3"
    assert state.attributes["venue"] is None

    assert state.attributes["form"] == ["loss", "win"]

    state = hass.states.get(STANDING)
    assert state.state == "2"
    assert state.attributes["points"] == 3
    assert state.attributes["points_to_leader"] == 0
    assert state.attributes["season_played"] == 2
    assert state.attributes["season_goals_for"] == 3
    assert state.attributes["teams"] == 4
    assert state.attributes["table"][0]["team"] == "SV Fictief JO13-1"

    # The match of two other teams in the programme is not the team's.
    assert calls_to(mock_api, "TeamPersons") == 0
    assert calls_to(mock_api, "match/MatchFacility") == 2  # the coming week only
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    assert coordinator.update_interval == UPDATE_INTERVAL  # next poll 30 min before kick-off
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coordinator.update_interval == UPDATE_INTERVAL_MATCH


async def test_calendar(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """All own matches of the season, except the called-off one."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    state = hass.states.get(CALENDAR)
    assert state.attributes["message"] == "VV Voorbeeld JO13-2 – SV Fictief JO13-1"
    assert state.attributes["location"] == "Sportpark Voorbeeld, Voorbeeldlaan 1, 1234 AB Voordorp"

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": "2026-08-01T00:00:00+00:00",
            "end_date_time": "2027-06-30T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response[CALENDAR]["events"]
    assert [e["summary"] for e in events] == [
        "VV Verzonnen JO13-1 – VV Voorbeeld JO13-2 (2–0)",
        "VV Voorbeeld JO13-2 – RKVV Denkbeeld JO13-3 (3–1)",
        "VV Voorbeeld JO13-2 – SV Fictief JO13-1",
        "RKVV Denkbeeld JO13-3 – VV Voorbeeld JO13-2",
    ]
    today = events[2]
    assert today["description"] == (
        "3e klasse (2e fase) – 24\nVeld: veld 2\nKleedkamer: 3\nVertrek: 09:15\nWedstrijdnummer: 1"
    )
    assert today["end"] == "2026-11-14T11:15:00+01:00"
    away = events[3]
    assert "Kleedkamer: 4" in away["description"]


async def test_finished_pool_fetched_rarely(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The current pool is fetched each update, a finished one twice a day."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    pool = "pool/PoolCompetitionData"
    assert calls_to(mock_api, pool) == 2
    assert calls_to(mock_api, "match/MatchInfo") == 2  # one per pool
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls_to(mock_api, pool) == 3
    assert calls_to(mock_api, "match/MatchInfo") == 2  # once a day


async def test_quiet_after_the_season(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Without upcoming matches the team is polled rarely."""
    freezer.move_to("2027-07-01T12:00:00+00:00")
    await _setup(hass, config_entry)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    assert coordinator.update_interval == UPDATE_INTERVAL_IDLE
    assert hass.states.get(NEXT_MATCH).state == "unknown"


async def test_session_ended_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A refused refresh token asks the user to log in again."""
    aioclient_mock.post(TOKEN_URL, status=HTTPStatus.BAD_REQUEST, json={"error": "invalid_grant"})
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_connection_error_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A server error means: try again later."""
    from custom_components.competitie_voetbal.api import TOKEN_URL as URL  # noqa: PLC0415

    aioclient_mock.post(URL, json={"access_token": "a", "refresh_token": "r"})
    aioclient_mock.get(data_url("team/TeamProgram"), status=HTTPStatus.BAD_GATEWAY)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_token_update_does_not_reload_but_new_team_does(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Storing a refresh token keeps the entry loaded; adding a team reloads it."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    runtime = config_entry.runtime_data
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_REFRESH_TOKEN: "other"}
    )
    await hass.async_block_till_done()
    assert config_entry.runtime_data is runtime

    hass.config_entries.async_add_subentry(
        config_entry,
        ConfigSubentry(
            data={**TEAM_DATA, "team_id": "T1000000003", "team_name": "VV Voorbeeld JO9-1"},
            subentry_type=SUBENTRY_TEAM,
            title="VV Voorbeeld JO9-1",
            unique_id="T1000000003",
        ),
    )
    mock_api.get(data_url("team/TeamProgram"), json=load_fixture("team_program.json"))
    mock_api.get(data_url("team/TeamCompetitionData"), json=load_fixture("team_competition.json"))
    await hass.async_block_till_done()
    assert config_entry.runtime_data is not runtime
    assert len(config_entry.runtime_data.coordinators) == 2


async def test_diagnostics(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics hide the email address and the refresh token."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["entry"] == {"email": "**REDACTED**", "refresh_token": "**REDACTED**"}
    (team,) = diagnostics["teams"].values()
    assert team["data"]["durations"] == {"2002": "1:15:00", "2001": "1:15:00"}
    assert len(team["data"]["matches"]) == 5


async def test_unload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The account unloads cleanly."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
