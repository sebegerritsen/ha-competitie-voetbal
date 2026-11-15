"""Tests for the config flow (account) and the subentry flow (teams)."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.voetbalcompetitie.api import TOKEN_URL
from custom_components.voetbalcompetitie.const import (
    CONF_CLUB_ID,
    CONF_REFRESH_TOKEN,
    CONF_TEAM_ID,
    DOMAIN,
    SUBENTRY_TEAM,
)

from .conftest import EMAIL, TEAM_ID, data_url, token_answer

pytestmark = pytest.mark.usefixtures("dutch_time")


async def _login(hass: HomeAssistant, password: str = "secret") -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: f" {EMAIL} ", CONF_PASSWORD: password}
    )


async def test_login_and_pick_favorites(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """Log in, keep the favourite team: one entry with a team subentry, no password."""
    result = await _login(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "favorites"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"teams": [TEAM_ID]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: "refresh-1"}
    entry = result["result"]
    assert entry.unique_id == EMAIL
    (subentry,) = entry.subentries.values()
    assert subentry.subentry_type == SUBENTRY_TEAM
    assert subentry.unique_id == TEAM_ID
    assert subentry.title == "VV Voorbeeld JO13-2"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_no_favorites(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Without favourites the account is added without teams."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(
        data_url("user/favorite/Favorites"), params={"v": "2"}, json={"FavoriteTeam": []}
    )
    result = await _login(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].subentries == {}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (HTTPStatus.BAD_REQUEST, {"error": "Inloggen niet mogelijk"}, "invalid_auth"),
        (HTTPStatus.BAD_GATEWAY, {}, "cannot_connect"),
    ],
)
async def test_login_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict,
    error: str,
) -> None:
    """Login problems are shown on the form."""
    aioclient_mock.post(TOKEN_URL, status=status, json=body)
    result = await _login(hass, "wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_account_already_configured(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """An account can be added once."""
    config_entry.add_to_hass(hass)
    result = await _login(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Logging in again stores a new refresh token."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL
    mock_api.clear_requests()
    mock_api.post(TOKEN_URL, status=HTTPStatus.BAD_REQUEST, json={})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    mock_api.clear_requests()
    mock_api.post(TOKEN_URL, json=token_answer(7))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-7"


async def _account_without_teams(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: "stored"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_add_favorite_team(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """A followed team can be added from the menu; the account reloads with it."""
    entry = await _account_without_teams(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TEAM), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "favorite"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TEAM_ID: TEAM_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("calendar.vv_voorbeeld_jo13_2") is not None

    # Added already: no favourites left, so the flow goes straight to the search.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TEAM), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "search"


async def test_search_club_and_team(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """Any team of any club: search, pick the club, pick the team."""
    entry = await _account_without_teams(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TEAM), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "nergens-niet"}
    )
    assert result["errors"] == {"query": "no_clubs_found"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "voorbeeld"}
    )
    assert result["step_id"] == "club"
    options = result["data_schema"].schema[CONF_CLUB_ID].config["options"]
    assert [o["label"] for o in options] == [
        "VV Voorbeeld · Voordorp",
        "VV Voorbeeldige Vrienden · Elders",
    ]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CLUB_ID: "BBXX01A"}
    )
    assert result["step_id"] == "team"
    options = result["data_schema"].schema[CONF_TEAM_ID].config["options"]
    # Seniors first, then youth in natural order.
    assert [o["value"] for o in options] == ["T1000000009", "T1000000003", "T1000000001"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TEAM_ID: "T1000000003"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "VV Voorbeeld JO9-1"


async def test_search_single_club_and_duplicate(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """One search hit skips the club list; a team that is set up already is refused."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TEAM), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "search"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "voordorp"}
    )
    assert result["step_id"] == "team"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_TEAM_ID: TEAM_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_add_team_needs_a_working_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Teams can't be added while the account is not loaded."""
    aioclient_mock.post(TOKEN_URL, status=HTTPStatus.BAD_REQUEST, json={"error": "invalid_grant"})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_TEAM), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_not_loaded"
