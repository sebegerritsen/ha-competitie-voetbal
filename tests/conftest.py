"""Fixtures for the KNVB Voetbal tests. All clubs, teams and accounts in them are made up."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_EMAIL
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.knvb_voetbal.api import DATA_URL, TOKEN_URL
from custom_components.knvb_voetbal.const import (
    CONF_CLUB_ID,
    CONF_CLUB_NAME,
    CONF_REFRESH_TOKEN,
    CONF_SPORT,
    CONF_TEAM_CODE,
    CONF_TEAM_ID,
    CONF_TEAM_NAME,
    DOMAIN,
    SUBENTRY_TEAM,
)

FIXTURES = Path(__file__).parent / "fixtures"

EMAIL = "fan@example.com"
CLUB_ID = "BBXX01A"
TEAM_ID = "T1000000001"
POOL_CURRENT = 2002
POOL_FINISHED = 2001

# Saturday 14 November 2026, 09:00 in the Netherlands: the team plays at 10:00.
NOW = "2026-11-14T08:00:00+00:00"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def token_answer(n: int = 1) -> dict[str, Any]:
    """Return a token answer with numbered tokens."""
    return {
        "access_token": f"access-{n}",
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": f"refresh-{n}",
        "scope": ["COMPETITION"],
    }


def data_url(path: str) -> str:
    """Return the full URL of an entity."""
    return DATA_URL + path


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture
async def dutch_time(hass: Any) -> None:
    """Run Home Assistant in Dutch time."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")


TEAM_DATA = {
    CONF_TEAM_ID: TEAM_ID,
    CONF_TEAM_NAME: "VV Voorbeeld JO13-2",
    CONF_TEAM_CODE: "JO13-2",
    CONF_CLUB_ID: CLUB_ID,
    CONF_CLUB_NAME: "VV Voorbeeld",
    CONF_SPORT: "Veld Algemeen/Zaterdag",
}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return an account with one team."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: "stored-refresh"},
        subentries_data=[
            ConfigSubentryData(
                data=TEAM_DATA,
                subentry_type=SUBENTRY_TEAM,
                title="VV Voorbeeld JO13-2",
                unique_id=TEAM_ID,
            )
        ],
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every endpoint the integration uses with the fixtures."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    team = {"PublicTeamId": TEAM_ID}
    answers = {
        ("user/favorite/Favorites", 2, ()): "favorites.json",
        ("club/Clubs", 1, ()): "clubs.json",
        ("club/ClubTeams", 1, (("ClubId", CLUB_ID),)): "club_teams.json",
        ("club/ClubProgram", 3, (("ClubId", CLUB_ID),)): "club_program.json",
        ("club/ClubMatchResults", 2, (("ClubId", CLUB_ID),)): "club_results.json",
        ("team/TeamProgram", 3, tuple(team.items())): "team_program.json",
        ("team/TeamCompetitionData", 2, tuple(team.items())): "team_competition.json",
        ("pool/PoolCompetitionData", 2, (("PoolId", str(POOL_CURRENT)),)): "pool_current.json",
        ("pool/PoolCompetitionData", 2, (("PoolId", str(POOL_FINISHED)),)): "pool_finished.json",
        ("match/MatchFacility", 2, (("PublicMatchId", "M100"),)): "facility.json",
        ("match/MatchFacility", 2, (("PublicMatchId", "M101"),)): "facility.json",
    }
    for (path, version, params), fixture in answers.items():
        aioclient_mock.get(
            data_url(path), params={"v": str(version), **dict(params)}, json=load_fixture(fixture)
        )
    aioclient_mock.get(data_url("match/MatchInfo"), json=load_fixture("match_info.json"))
    return aioclient_mock


def calls_to(mock: AiohttpClientMocker, path: str) -> int:
    """Return how many requests went to a path (any query)."""
    return sum(1 for _m, url, _d, _h in mock.mock_calls if str(url).split("?")[0].endswith(path))
