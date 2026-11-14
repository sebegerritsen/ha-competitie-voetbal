"""Tests for clubs, statistics, periods, actions and the repair when the API changes."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentryData
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.competitie_voetbal.const import (
    CONF_CLUB_ID,
    CONF_CLUB_NAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    SUBENTRY_CLUB,
)
from custom_components.competitie_voetbal.coordinator import estimated_duration
from custom_components.competitie_voetbal.models import TeamRef

from .conftest import CLUB_ID, EMAIL, NOW, TEAM_ID, data_url, load_fixture

CLUB_CALENDAR = "calendar.vv_voorbeeld"
TODAY = "sensor.vv_voorbeeld_matches_today"
HOME_TODAY = "sensor.vv_voorbeeld_home_matches_today"
RESULTS = "sensor.vv_voorbeeld_latest_results"
STANDING = "sensor.vv_voorbeeld_jo13_2_standing"
POINTS = "sensor.vv_voorbeeld_jo13_2_points"

pytestmark = pytest.mark.usefixtures("dutch_time")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def club_entry() -> MockConfigEntry:
    """Return an account that follows a club."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: "stored-refresh"},
        subentries_data=[
            ConfigSubentryData(
                data={CONF_CLUB_ID: CLUB_ID, CONF_CLUB_NAME: "VV Voorbeeld"},
                subentry_type=SUBENTRY_CLUB,
                title="VV Voorbeeld",
                unique_id=CLUB_ID,
            )
        ],
    )


async def test_club(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    club_entry: MockConfigEntry,
) -> None:
    """A followed club: calendar, today's (home) matches, latest results."""
    freezer.move_to(NOW)
    await _setup(hass, club_entry)

    state = hass.states.get(TODAY)
    assert state.state == "3"
    assert state.attributes["first_kickoff"] == "2026-11-14T09:00:00+01:00"
    assert state.attributes["last_kickoff"] == "2026-11-14T14:30:00+01:00"
    away = state.attributes["matches"][2]
    assert (away["team"], away["opponent"], away["home_away"]) == (
        "VV Voorbeeld 1",
        "SV Fictief 1",
        "away",
    )
    assert hass.states.get(HOME_TODAY).state == "2"
    assert hass.states.get(TODAY).attributes["entity_picture"].endswith("BBXX01A")

    state = hass.states.get(RESULTS)
    assert state.state == "2026-11-07"
    assert (state.attributes["won"], state.attributes["lost"]) == (1, 1)

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CLUB_CALENDAR,
            "start_date_time": "2026-10-01T00:00:00+00:00",
            "end_date_time": "2026-12-01T00:00:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    events = response[CLUB_CALENDAR]["events"]
    assert len(events) == 7
    assert events[0]["summary"] == "VV Voorbeeld JO9-1 – SV Fictief JO9-1 (3–1)"
    # A JO9 match: 50 minutes playing time plus half-time.
    assert events[0]["end"] == "2026-10-31T10:05:00+01:00"


async def test_add_club(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, mock_api: AiohttpClientMocker
) -> None:
    """Follow a club from the account's page; a second time is refused."""
    freezer.move_to(NOW)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: "stored"},
    )
    await _setup(hass, entry)
    for expected in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_CLUB), context={"source": SOURCE_USER}
        )
        assert result["step_id"] == "search"
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"query": "voorbeeld"}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_CLUB_ID: CLUB_ID}
        )
        assert result["type"] is expected
        await hass.async_block_till_done()
    assert result["reason"] == "already_configured"
    assert hass.states.get(TODAY).state == "3"

    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_CLUB),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["reason"] == "no_settings"


async def test_statistics_periods_last_round(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Points as a statistic; the standing has periods and the latest round."""
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    state = hass.states.get(POINTS)
    assert state.state == "3"
    assert state.attributes["state_class"] == "measurement"
    assert hass.states.get("sensor.vv_voorbeeld_jo13_2_goals_for") is None  # off by default
    state = hass.states.get(STANDING)
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["entity_picture"].endswith("BBXX01A")
    assert state.attributes["periods"] == [
        {"period": 1, "position": 2, "points": 3, "played": 1, "leader": "SV Fictief JO13-1"}
    ]
    assert [r["score"] for r in state.attributes["last_round"]] == ["3–1", "2–2"]
    attrs = hass.states.get("sensor.vv_voorbeeld_jo13_2_next_match").attributes
    assert attrs["match_number"] == 1
    assert attrs["opponent_colours"] is None


async def test_assembly_time_comes_first(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The club's meet time (verzameltijd) wins over its departure time."""
    facility = load_fixture("facility.json") | {"AssemblyTime": "09:30:00"}
    mock_api._mocks = [m for m in mock_api._mocks if "MatchFacility" not in str(m._url)]
    mock_api.get(data_url("match/MatchFacility"), json=facility)
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    state = hass.states.get("sensor.vv_voorbeeld_jo13_2_meet_time")
    assert state.state == "2026-11-14T08:30:00+00:00"
    assert state.attributes["assembly_time"] == "09:30"
    assert state.attributes["departure_time"] == "09:15"


async def test_actions(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    club_entry: MockConfigEntry,
) -> None:
    """get_matches for a team and a club, get_standing for a team."""
    freezer.move_to(NOW)
    both = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data=dict(config_entry.data),
        subentries_data=[
            ConfigSubentryData(
                data=dict(sub.data),
                subentry_type=sub.subentry_type,
                title=sub.title,
                unique_id=sub.unique_id,
            )
            for sub in (*config_entry.subentries.values(), *club_entry.subentries.values())
        ],
    )
    await _setup(hass, both)
    devices = dr.async_get(hass)
    (team,) = devices.async_get_devices(identifiers={(DOMAIN, TEAM_ID)})
    (club,) = devices.async_get_devices(identifiers={(DOMAIN, f"club_{CLUB_ID}")})

    response = await hass.services.async_call(
        DOMAIN, "get_matches", {"device_id": team.id}, blocking=True, return_response=True
    )
    ids = [m["match_id"] for m in response["matches"]]
    assert ids == ["M081", "M080", "M090", "M100", "M101"]
    assert response["matches"][0]["status"] == "cancelled"
    assert response["matches"][2]["score"] == "3–1"
    response = await hass.services.async_call(
        DOMAIN,
        "get_matches",
        {"device_id": team.id, "upcoming_only": True},
        blocking=True,
        return_response=True,
    )
    assert [m["match_id"] for m in response["matches"]] == ["M100", "M101"]
    response = await hass.services.async_call(
        DOMAIN,
        "get_matches",
        {"device_id": club.id, "upcoming_only": True},
        blocking=True,
        return_response=True,
    )
    assert len(response["matches"]) == 4

    response = await hass.services.async_call(
        DOMAIN, "get_standing", {"device_id": team.id}, blocking=True, return_response=True
    )
    assert response["position"] == 2
    assert response["table"][0]["form"] == ["draw"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_standing", {"device_id": club.id}, blocking=True, return_response=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_matches", {"device_id": "nope"}, blocking=True, return_response=True
        )


async def test_api_changed_repair(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A 603 (the app moved on) shows a repair; it goes away when requests work again."""
    mock_api._mocks = [m for m in mock_api._mocks if "TeamProgram" not in str(m._url)]
    mock_api.get(data_url("team/TeamProgram"), status=603, text="{}")
    freezer.move_to(NOW)
    await _setup(hass, config_entry)
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "api_changed") is not None

    mock_api._mocks = [m for m in mock_api._mocks if "TeamProgram" not in str(m._url)]
    mock_api.get(data_url("team/TeamProgram"), json=load_fixture("team_program.json"))
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "api_changed") is None


def test_estimated_duration() -> None:
    """Without MatchInfo, the age group in the team code sets the length."""
    assert estimated_duration(TeamRef(id="t", name="x", code="JO8-1")) == timedelta(minutes=55)
    assert estimated_duration(TeamRef(id="t", name="x", code="O13-2")) == timedelta(minutes=75)
    assert estimated_duration(TeamRef(id="t", name="x", code="MO17-1")) == timedelta(minutes=95)
    assert estimated_duration(TeamRef(id="t", name="x", code="1")) == timedelta(minutes=105)
    assert estimated_duration(TeamRef(id="t", name="x")) == timedelta(minutes=105)


async def test_season_history_into_statistics(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Graphs start at the season's beginning: results go into the long-term statistics."""
    from unittest.mock import patch  # noqa: PLC0415

    freezer.move_to(NOW)
    hass.config.components.add("recorder")
    with patch(
        "custom_components.competitie_voetbal.sensor.async_import_statistics"
    ) as import_statistics:
        await _setup(hass, config_entry)
    imported = {
        call.args[1]["statistic_id"]: [
            (row["start"].isoformat(), row["mean"]) for row in call.args[2]
        ]
        for call in import_statistics.call_args_list
    }
    # One result in the current pool: 7 Nov, 08:30 + 75 min (local) → the 08:00 UTC hour,
    # then a row at noon (local) every day until an hour ago, so graphs draw a line.
    points = imported[POINTS]
    assert points[0] == ("2026-11-07T08:00:00+00:00", 3)
    assert [start[:13] for start, _ in points[1:]] == [
        f"2026-11-{day:02d}T11" for day in range(7, 14)
    ]
    assert {value for _, value in points} == {3}
    assert imported["sensor.vv_voorbeeld_jo13_2_goal_difference"][0] == (
        "2026-11-07T08:00:00+00:00",
        2,
    )
    assert "sensor.vv_voorbeeld_jo13_2_goals_for" not in imported  # disabled entity
    results = hass.states.get("sensor.vv_voorbeeld_jo13_2_last_result").attributes
    assert [r["score"] for r in results["season_results"]] == ["2–0", "3–1"]


def test_history_rows_step_between_matches() -> None:
    """Between two matches the daily rows keep the earlier value; the later one takes over."""
    from datetime import datetime  # noqa: PLC0415

    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    from custom_components.competitie_voetbal.sensor import history_rows  # noqa: PLC0415

    utc = dt_util.UTC
    history = [
        (datetime(2026, 10, 31, 8, 45, tzinfo=utc), 1),
        (datetime(2026, 11, 2, 9, 30, tzinfo=utc), 4),
    ]
    rows = history_rows(history, datetime(2026, 11, 3, 23, 0, tzinfo=utc))
    assert [(start.isoformat()[:13], value) for start, value in rows] == [
        ("2026-10-31T08", 1),
        ("2026-10-31T11", 1),
        ("2026-11-01T11", 1),
        ("2026-11-02T09", 4),
        ("2026-11-02T11", 4),
        ("2026-11-03T11", 4),
    ]
    assert history_rows([], datetime(2026, 11, 3, tzinfo=utc)) == []
