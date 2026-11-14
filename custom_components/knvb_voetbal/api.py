"""Async client for the API behind the voetbal.nl app (Sportlink, KNVB); see docs/api.md.

The integration logs in the way the app does: with the app's fixed OAuth client id and
secret, and an app-style User-Agent (the server refuses other user agents). After the
first login it only keeps a refresh token. Refresh tokens are single use: every refresh
returns a new one, which is handed to `on_refresh_token` to be stored at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from http import HTTPStatus
import logging
from typing import Any

import aiohttp

from .models import (
    Club,
    Facility,
    Match,
    PoolData,
    TeamCompetition,
    TeamRef,
    parse_club_teams,
    parse_clubs,
    parse_favorite_teams,
    parse_playing_time,
    parse_program,
)

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://app-vnl-production.sportlink.com"
TOKEN_URL = f"{BASE_URL}/oauth/token"
DATA_URL = f"{BASE_URL}/entity/common/memberportal/app/"

# The voetbal.nl app (com.xs2theworld.voetballNL). An app update may change these.
APP_VERSION = "6.64.0"
APP_BUILD = "2026025447"
USER_AGENT = (
    f"sportlink-app-voetbalnl/{APP_VERSION}-{APP_BUILD} android generic/android/33  ({APP_VERSION})"
)
CLIENT_ID = "oCuV9oozaaz8zee"
CLIENT_SECRET = "eep7Shoo7i"  # noqa: S105 - the app's public client secret

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class KnvbError(Exception):
    """Base class for errors talking to the API."""


class KnvbConnectionError(KnvbError):
    """The API could not be reached, timed out or had a server error."""


class KnvbAuthError(KnvbError):
    """The session is no longer valid: log in again."""


class KnvbInvalidCredentials(KnvbAuthError):
    """The email address or password is wrong."""


class KnvbNotFoundError(KnvbError):
    """The requested club, team, pool or match does not exist (any more)."""


class KnvbResponseError(KnvbError):
    """The API answered something unexpected."""


class KnvbClient:
    """Client for one voetbal.nl account, shared by all its teams."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        refresh_token: str | None = None,
        on_refresh_token: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the client with a stored refresh token, or log in later."""
        self._session = session
        self.refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._access_token: str | None = None
        self._lock = asyncio.Lock()
        # path -> (etag, answer), for conditional requests
        self._etags: dict[str, tuple[str, Any]] = {}

    async def login(self, email: str, password: str) -> None:
        """Log in with email and password (used once; only the refresh token is kept)."""
        async with self._lock:
            await self._token_request(
                {"grant_type": "password", "username": email, "password": password}
            )

    async def _refresh(self, used_token: str | None) -> None:
        """Get a new access token, unless another request already did."""
        async with self._lock:
            if self._access_token is not None and self._access_token != used_token:
                return
            if not self.refresh_token:
                raise KnvbAuthError("No refresh token")
            await self._token_request(
                {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
            )

    async def _token_request(self, fields: dict[str, str]) -> None:
        form = {**fields, "client_id": CLIENT_ID, "secret": CLIENT_SECRET}
        try:
            async with self._session.post(
                TOKEN_URL,
                data=form,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KnvbConnectionError(str(err) or type(err).__name__) from err
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise KnvbConnectionError(f"Server error {status} on login")
        if status >= HTTPStatus.BAD_REQUEST:
            if fields["grant_type"] == "password":
                raise KnvbInvalidCredentials(f"Login refused ({status})")
            self._access_token = None
            raise KnvbAuthError(f"Session refused ({status})")
        if not isinstance(data, dict):
            raise KnvbResponseError("Login answer is not JSON")
        access, refresh = data.get("access_token"), data.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str) or not access:
            raise KnvbResponseError("No tokens in the login answer")
        self._access_token = access
        self.refresh_token = refresh
        if self._on_refresh_token is not None:
            self._on_refresh_token(refresh)

    async def get(self, path: str, version: int, params: Mapping[str, str] | None = None) -> Any:
        """Send a GET request for an entity and return the JSON answer."""
        if "person" in path.lower():
            # Rosters and other person data list minors: never fetched.
            raise ValueError(f"Refusing to fetch person data: {path}")
        if self._access_token is None:
            await self._refresh(None)
        query = {"v": str(version), **(params or {})}
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        for attempt in range(2):
            used_token = self._access_token
            headers = {
                "Authorization": f"Bearer {used_token}",
                "X-Navajo-Instance": "KNVB",
                "X-Navajo-Version": str(version),  # must equal ?v=
                "X-Navajo-Locale": "nl",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
            if cached := self._etags.get(cache_key):
                headers["If-None-Match"] = cached[0]
            try:
                async with self._session.get(
                    DATA_URL + path, params=query, headers=headers, timeout=REQUEST_TIMEOUT
                ) as resp:
                    status = resp.status
                    if status == HTTPStatus.NOT_MODIFIED and cached:
                        return cached[1]
                    if status == HTTPStatus.UNAUTHORIZED and attempt == 0:
                        await self._refresh(used_token)
                        continue
                    _check_status(status, path)
                    try:
                        data = await resp.json(content_type=None)
                    except ValueError as err:
                        raise KnvbResponseError(f"Answer to {path} is not JSON") from err
                    if etag := resp.headers.get("ETag"):
                        self._etags[cache_key] = (etag, data)
                    return data
            except (aiohttp.ClientError, TimeoutError) as err:
                raise KnvbConnectionError(str(err) or type(err).__name__) from err
        raise KnvbAuthError("The API refused the session")  # pragma: no cover

    async def get_favorite_teams(self) -> list[TeamRef]:
        """Return the teams the account follows in the app."""
        return parse_favorite_teams(await self.get("user/favorite/Favorites", 2))

    async def get_clubs(self) -> list[Club]:
        """Return all clubs (about 2,800)."""
        return parse_clubs(await self.get("club/Clubs", 1))

    async def get_club_teams(self, club_id: str) -> list[TeamRef]:
        """Return a club's teams."""
        return parse_club_teams(await self.get("club/ClubTeams", 1, {"ClubId": club_id}))

    async def get_team_program(self, team_id: str) -> list[Match]:
        """Return the upcoming matches of the team's pools (not only the team's own)."""
        return parse_program(await self.get("team/TeamProgram", 3, {"PublicTeamId": team_id}))

    async def get_team_competition(self, team_id: str) -> TeamCompetition:
        """Return the team's pools this season, and results outside them (friendlies)."""
        answer = await self.get("team/TeamCompetitionData", 2, {"PublicTeamId": team_id})
        return TeamCompetition.from_api(_dict(answer))

    async def get_pool(self, pool_id: int) -> PoolData:
        """Return a pool's results and standing."""
        answer = await self.get("pool/PoolCompetitionData", 2, {"PoolId": str(pool_id)})
        return PoolData.from_api(_dict(answer))

    async def get_facility(self, match_id: str) -> Facility:
        """Return where a match is played: venue, address, pitch, dressing rooms."""
        answer = await self.get("match/MatchFacility", 2, {"PublicMatchId": match_id})
        return Facility.from_api(_dict(answer))

    async def get_playing_time(self, match_id: str) -> tuple[int | None, int | None]:
        """Return the net and gross playing time of a match's class, in minutes."""
        answer = await self.get("match/MatchInfo", 1, {"PublicMatchId": match_id})
        return parse_playing_time(_dict(answer))


def _check_status(status: int, path: str) -> None:
    """Raise the error that belongs to an HTTP status, if any."""
    if status == HTTPStatus.UNAUTHORIZED:
        raise KnvbAuthError("The API refused the session")
    if status == HTTPStatus.NOT_FOUND:
        raise KnvbNotFoundError(f"Not found: {path}")
    if HTTPStatus.INTERNAL_SERVER_ERROR <= status < 600:
        raise KnvbConnectionError(f"Server error {status} on {path}")
    if status >= HTTPStatus.BAD_REQUEST:
        # Also 603 "Unknown entity version": the app's API has moved on.
        raise KnvbResponseError(f"Unexpected status {status} on {path}")


def _dict(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict):
        raise KnvbResponseError("Expected a JSON object")
    return answer
