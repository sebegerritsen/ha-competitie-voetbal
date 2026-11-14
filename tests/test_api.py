"""Tests for the API client: login, single-use refresh tokens, headers, errors."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.knvb_voetbal.api import (
    APP_VERSION,
    TOKEN_URL,
    KnvbAuthError,
    KnvbClient,
    KnvbConnectionError,
    KnvbInvalidCredentials,
    KnvbNotFoundError,
    KnvbResponseError,
)

from .conftest import EMAIL, data_url, load_fixture, token_answer

FAVORITES = data_url("user/favorite/Favorites")


def _client(hass: HomeAssistant, stored: list[str], token: str | None = "r0") -> KnvbClient:
    return KnvbClient(
        async_get_clientsession(hass), refresh_token=token, on_refresh_token=stored.append
    )


async def test_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Logging in sends the app's client and keeps only the refresh token."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    stored: list[str] = []
    client = _client(hass, stored, None)
    await client.login(EMAIL, "secret")
    assert client.refresh_token == "refresh-1"
    assert stored == ["refresh-1"]
    form = aioclient_mock.mock_calls[0][2]
    assert form["grant_type"] == "password"
    assert form["username"] == EMAIL
    assert form["client_id"]
    assert form["secret"]


async def test_wrong_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A refused password login is an invalid-credentials error."""
    aioclient_mock.post(
        TOKEN_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={"error": "Inloggen niet mogelijk", "description": "onjuiste inloggegevens"},
    )
    with pytest.raises(KnvbInvalidCredentials):
        await _client(hass, [], None).login(EMAIL, "wrong")


async def test_refresh_and_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first request refreshes; requests carry the app's headers."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, json=load_fixture("favorites.json"))
    stored: list[str] = []
    client = _client(hass, stored)
    teams = await client.get_favorite_teams()
    assert [team.id for team in teams] == ["T1000000001"]
    assert stored == ["refresh-1"]
    assert aioclient_mock.mock_calls[0][2]["refresh_token"] == "r0"
    _method, url, _data, headers = aioclient_mock.mock_calls[1]
    assert url.query["v"] == "2"
    assert headers["X-Navajo-Version"] == "2"
    assert headers["X-Navajo-Instance"] == "KNVB"
    assert headers["Authorization"] == "Bearer access-1"
    assert headers["User-Agent"].startswith("sportlink-app-voetbalnl/")
    assert headers["User-Agent"].endswith(f"({APP_VERSION})")


async def test_expired_access_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 refreshes once and retries."""
    calls = 0

    async def _favorites(method, url, data):
        nonlocal calls
        calls += 1
        status = HTTPStatus.UNAUTHORIZED if calls == 1 else HTTPStatus.OK
        return AiohttpClientMockResponse(
            method, url, status=status, json=load_fixture("favorites.json")
        )

    from pytest_homeassistant_custom_component.test_util.aiohttp import (  # noqa: PLC0415
        AiohttpClientMockResponse,
    )

    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, side_effect=_favorites)
    stored: list[str] = []
    client = _client(hass, stored)
    await client.get_favorite_teams()
    assert calls == 2
    assert len(stored) == 2  # once at the start, once after the 401


async def test_refresh_token_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """invalid_grant means: log in again."""
    aioclient_mock.post(TOKEN_URL, status=HTTPStatus.BAD_REQUEST, json={"error": "invalid_grant"})
    with pytest.raises(KnvbAuthError) as err:
        await _client(hass, []).get_favorite_teams()
    assert not isinstance(err.value, KnvbInvalidCredentials)


async def test_parallel_requests_refresh_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Refresh tokens are single use: parallel requests must not refresh twice."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, json=load_fixture("favorites.json"))
    stored: list[str] = []
    client = _client(hass, stored)
    await asyncio.gather(*(client.get_favorite_teams() for _ in range(3)))
    assert stored == ["refresh-1"]


async def test_etag(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 304 answer reuses the previous one."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, json=load_fixture("favorites.json"), headers={"ETag": "abc"})
    client = _client(hass, [])
    first = await client.get_favorite_teams()
    aioclient_mock.clear_requests()
    aioclient_mock.get(FAVORITES, status=HTTPStatus.NOT_MODIFIED)
    assert await client.get_favorite_teams() == first
    assert aioclient_mock.mock_calls[0][3]["If-None-Match"] == "abc"


@pytest.mark.parametrize(
    ("status", "exc", "error"),
    [
        (HTTPStatus.NOT_FOUND, None, KnvbNotFoundError),
        (HTTPStatus.BAD_GATEWAY, None, KnvbConnectionError),
        (603, None, KnvbResponseError),
        (HTTPStatus.OK, aiohttp.ClientError(), KnvbConnectionError),
        (HTTPStatus.OK, TimeoutError(), KnvbConnectionError),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    exc: Exception | None,
    error: type[Exception],
) -> None:
    """HTTP problems become typed errors."""
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, status=status, exc=exc, text="{}")
    with pytest.raises(error):
        await _client(hass, []).get_favorite_teams()


async def test_unexpected_answers(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Non-JSON and token-less answers are response errors."""
    aioclient_mock.post(TOKEN_URL, json={"token_type": "bearer"})
    with pytest.raises(KnvbResponseError):
        await _client(hass, []).get_favorite_teams()
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, json=token_answer())
    aioclient_mock.get(FAVORITES, text="not json")
    with pytest.raises(KnvbResponseError):
        await _client(hass, []).get_favorite_teams()
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, status=HTTPStatus.SERVICE_UNAVAILABLE, text="")
    with pytest.raises(KnvbConnectionError):
        await _client(hass, []).get_favorite_teams()


async def test_never_fetches_person_data(hass: HomeAssistant) -> None:
    """Rosters list minors: the client refuses person endpoints outright."""
    with pytest.raises(ValueError, match="person"):
        await _client(hass, []).get("team/TeamPersons", 1, {"PublicTeamId": "T1"})
