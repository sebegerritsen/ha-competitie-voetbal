"""Tests for parsing the API's answers."""

from __future__ import annotations

from datetime import timedelta

from custom_components.competitie_voetbal.coordinator import match_duration
from custom_components.competitie_voetbal.models import (
    Facility,
    MatchStatus,
    PoolData,
    TeamCompetition,
    parse_club_teams,
    parse_clubs,
    parse_program,
)

from .conftest import TEAM_ID, load_fixture


def test_program() -> None:
    """The programme holds the whole pool; the time zone is the API's."""
    matches = parse_program(load_fixture("team_program.json"))
    assert [m.id for m in matches] == ["M100", "M101", "M900"]
    first = matches[0]
    assert first.start.isoformat() == "2026-11-14T10:00:00+01:00"
    assert first.status is MatchStatus.SCHEDULED
    assert first.venue == "Sportpark Voorbeeld"
    assert first.pool.label == "3e klasse (2e fase) – 24"
    assert first.is_home(TEAM_ID)
    assert first.opponent(TEAM_ID).name == "SV Fictief JO13-1"
    assert not matches[2].involves(TEAM_ID)


def test_results_and_standing() -> None:
    """Results have scores; a called-off match says so in DisciplineStatus."""
    pool = PoolData.from_api(load_fixture("pool_finished.json"))
    played, called_off = pool.results
    assert played.has_result
    assert played.goals(TEAM_ID) == (0, 2)
    assert called_off.status is MatchStatus.CANCELLED
    assert called_off.status_note == "Afgelast door vereniging"
    assert not called_off.has_result

    current = PoolData.from_api(load_fixture("pool_current.json"))
    assert [row.position for row in current.standings] == [1, 2, 3, 4]
    own = next(row for row in current.standings if row.team.id == TEAM_ID)
    assert (own.points, own.goal_difference) == (3, 2)


def test_extra_time_counts_for_the_result() -> None:
    """A cup match decided in extra time counts as won by the extra-time score."""
    data = load_fixture("pool_current.json")["MatchResult"][0]
    data["HomeResult"] = {"Score": 1, "ExtensionScore": 2, "PenaltyScore": None}
    data["AwayResult"] = {"Score": 1, "ExtensionScore": 1, "PenaltyScore": None}
    match = PoolData.from_api({"MatchResult": [data]}).results[0]
    assert match.goals(TEAM_ID) == (2, 1)


def test_current_pool() -> None:
    """The pool with the next match is current; without one, the latest played."""
    competition = TeamCompetition.from_api(load_fixture("team_competition.json"))
    assert competition.current_pool().id == 2002
    data = load_fixture("team_competition.json")
    data["Pool"][1]["NextMatchDate"] = None
    assert TeamCompetition.from_api(data).current_pool().id == 2002
    assert TeamCompetition.from_api({}).current_pool() is None


def test_clubs_teams_facility() -> None:
    """Towns are written normally; the address is complete."""
    clubs = parse_clubs(load_fixture("clubs.json"))
    assert clubs[0].city == "Voordorp"
    assert clubs[3].city == "Dorp Aan Zee"
    assert clubs[0].matches_query("voorbeeld")
    assert not clubs[0].matches_query("  ")
    teams = parse_club_teams(load_fixture("club_teams.json"))
    assert teams[0].classes == ("4e klasse",)
    facility = Facility.from_api(load_fixture("facility.json"))
    assert facility.full_address == "Voorbeeldlaan 1, 1234 AB Voordorp"
    assert facility.departure_time == "09:15"
    assert facility.pitch == "veld 2"


def test_lenient_parsing() -> None:
    """Garbage gives empty results instead of exceptions."""
    assert parse_program(None) == []
    assert parse_program({"ProgramItemMatch": [None, {"Match": {"PublicMatchId": "M1"}}]}) == []
    assert PoolData.from_api({"PoolStanding": "x"}) == PoolData()
    assert Facility.from_api({}).full_address is None


def test_match_duration() -> None:
    """Playing time plus half-time, never longer than the booked slot."""
    assert match_duration(60, 105) == timedelta(minutes=75)
    assert match_duration(90, 135) == timedelta(minutes=105)
    assert match_duration(20, 30) == timedelta(minutes=30)
    assert match_duration(None, 80) == timedelta(minutes=80)
    assert match_duration(None, None) == timedelta(minutes=105)
