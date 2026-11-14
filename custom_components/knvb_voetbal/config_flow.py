"""Config flow: log in with a voetbal.nl account, then add teams as subentries."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import KnvbClient, KnvbError, KnvbInvalidCredentials
from .const import (
    CONF_CLUB_ID,
    CONF_CLUB_NAME,
    CONF_MEET_MINUTES_AWAY,
    CONF_MEET_MINUTES_HOME,
    CONF_REFRESH_TOKEN,
    CONF_SPORT,
    CONF_TEAM_CODE,
    CONF_TEAM_ID,
    CONF_TEAM_NAME,
    CONF_WEATHER_ENTITY,
    DEFAULT_MEET_MINUTES,
    DOMAIN,
    LOGGER,
    SUBENTRY_TEAM,
)
from .models import Club, TeamRef

CONF_TEAMS = "teams"
CONF_QUERY = "query"
# Show a short list as radio buttons, a longer one as a dropdown.
LIST_MODE_MAX = 6

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
LOGIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)


MEET_MINUTES_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0, max=240, step=5, unit_of_measurement="min", mode=NumberSelectorMode.BOX
    )
)


def club_label(club: Club) -> str:
    """Return a club's label, such as `VV Voorbeeld · Utrecht`."""
    return f"{club.name} · {club.city}" if club.city else club.name


def team_label(team: TeamRef) -> str:
    """Return a team's label, such as `VV Voorbeeld JO13-2 · Veld Algemeen/Zaterdag`."""
    return " · ".join(part for part in (team.name, team.sport) if part)


def _natural_key(text: str) -> list[Any]:
    """Sort JO9 before JO10: compare the digits in a name as numbers."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def team_sort_key(team: TeamRef) -> tuple[Any, ...]:
    """Sort senior teams (`1`, `2`) before youth teams, then naturally by code."""
    code = team.code or team.name
    return (not code[:1].isdigit(), _natural_key(code), team.sport or "")


def team_subentry(team: TeamRef) -> ConfigSubentryData:
    """Return the subentry data of a team."""
    return ConfigSubentryData(
        data={
            CONF_TEAM_ID: team.id,
            CONF_TEAM_NAME: team.name,
            CONF_TEAM_CODE: team.code,
            CONF_CLUB_ID: team.club_id,
            CONF_CLUB_NAME: team.club_name,
            CONF_SPORT: team.sport,
        },
        subentry_type=SUBENTRY_TEAM,
        title=team.name,
        unique_id=team.id,
    )


def _team_selector(teams: list[TeamRef], *, multiple: bool = False) -> SelectSelector:
    options = [SelectOptionDict(value=team.id, label=team_label(team)) for team in teams]
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=multiple,
            mode=SelectSelectorMode.LIST
            if len(options) <= LIST_MODE_MAX
            else SelectSelectorMode.DROPDOWN,
        )
    )


class KnvbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in with a voetbal.nl account; optionally add the teams it follows."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._client: KnvbClient | None = None
        self._email: str | None = None
        self._favorites: list[TeamRef] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Teams are added to an account as subentries."""
        return {SUBENTRY_TEAM: TeamSubentryFlow}

    async def _login(self, email: str, password: str) -> dict[str, str]:
        """Log in; return form errors (empty on success)."""
        self._client = KnvbClient(async_get_clientsession(self.hass))
        try:
            await self._client.login(email, password)
        except KnvbInvalidCredentials:
            return {"base": "invalid_auth"}
        except KnvbError as err:
            LOGGER.debug("Login failed: %s", err)
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the voetbal.nl account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.casefold())
            self._abort_if_unique_id_configured()
            errors = await self._login(email, user_input[CONF_PASSWORD])
            if not errors:
                self._email = email
                assert self._client is not None
                try:
                    self._favorites = sorted(
                        await self._client.get_favorite_teams(), key=team_sort_key
                    )
                except KnvbError as err:
                    LOGGER.debug("Could not fetch favourites: %s", err)
                if self._favorites:
                    return await self.async_step_favorites()
                return self._create_entry([])
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)}
            ),
            errors=errors,
        )

    async def async_step_favorites(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which of the account's favourite teams to add."""
        if user_input is not None:
            chosen = set(user_input.get(CONF_TEAMS, []))
            return self._create_entry([team for team in self._favorites if team.id in chosen])
        return self.async_show_form(
            step_id="favorites",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEAMS, default=[team.id for team in self._favorites]
                    ): _team_selector(self._favorites, multiple=True)
                }
            ),
        )

    def _create_entry(self, teams: list[TeamRef]) -> ConfigFlowResult:
        assert self._client is not None and self._client.refresh_token is not None
        assert self._email is not None
        return self.async_create_entry(
            title=self._email,
            data={CONF_EMAIL: self._email, CONF_REFRESH_TOKEN: self._client.refresh_token},
            subentries=[team_subentry(team) for team in teams],
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The session ended: log in again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if not errors:
                assert self._client is not None and self._client.refresh_token is not None
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_REFRESH_TOKEN: self._client.refresh_token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class TeamSubentryFlow(ConfigSubentryFlow):
    """Add a team: from the account's favourites, or any team of any club."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._favorites: list[TeamRef] = []
        self._clubs: list[Club] = []
        self._found: list[Club] = []
        self._club: Club | None = None
        self._teams: list[TeamRef] = []

    @property
    def _client(self) -> KnvbClient:
        return self._get_entry().runtime_data.client

    def _configured(self) -> set[str]:
        return {
            subentry.unique_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            for subentry in entry.subentries.values()
            if subentry.unique_id
        }

    def _add(self, team: TeamRef) -> SubentryFlowResult:
        if team.id in self._configured():
            return self.async_abort(reason="already_configured")
        subentry = team_subentry(team)
        return self.async_create_entry(
            title=subentry["title"], data=subentry["data"], unique_id=subentry["unique_id"]
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Offer the favourites not added yet, or a club search."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="account_not_loaded")
        try:
            favorites = await self._client.get_favorite_teams()
        except KnvbError as err:
            LOGGER.debug("Could not fetch favourites: %s", err)
            favorites = []
        configured = self._configured()
        self._favorites = sorted(
            (team for team in favorites if team.id not in configured), key=team_sort_key
        )
        if not self._favorites:
            return await self.async_step_search()
        return self.async_show_menu(step_id="user", menu_options=["favorite", "search"])

    async def async_step_favorite(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick one of the account's favourite teams."""
        if user_input is not None:
            team = next(t for t in self._favorites if t.id == user_input[CONF_TEAM_ID])
            return self._add(team)
        return self.async_show_form(
            step_id="favorite",
            data_schema=vol.Schema({vol.Required(CONF_TEAM_ID): _team_selector(self._favorites)}),
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for (part of) the club's name or town."""
        errors: dict[str, str] = {}
        if user_input is not None:
            query = user_input[CONF_QUERY].strip()
            try:
                if not self._clubs:
                    self._clubs = await self._client.get_clubs()
            except KnvbError as err:
                LOGGER.debug("Could not fetch clubs: %s", err)
                errors["base"] = "cannot_connect"
            else:
                self._found = sorted(
                    (club for club in self._clubs if club.matches_query(query)),
                    key=lambda club: club.name.casefold(),
                )
                if not self._found:
                    errors[CONF_QUERY] = "no_clubs_found"
                elif len(self._found) == 1:
                    return await self.async_step_club({CONF_CLUB_ID: self._found[0].id})
                else:
                    return await self.async_step_club()
        return self.async_show_form(
            step_id="search",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_QUERY): TextSelector()}), user_input
            ),
            errors=errors,
        )

    async def async_step_club(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the club from the search results."""
        errors: dict[str, str] = {}
        if user_input is not None:
            club = next(c for c in self._found if c.id == user_input[CONF_CLUB_ID])
            try:
                teams = await self._client.get_club_teams(club.id)
            except KnvbError as err:
                LOGGER.debug("Could not fetch teams of %s: %s", club.id, err)
                errors["base"] = "cannot_connect"
            else:
                self._club = club
                self._teams = sorted(teams, key=team_sort_key)
                if not self._teams:
                    return self.async_abort(
                        reason="no_teams", description_placeholders={"club": club.name}
                    )
                return await self.async_step_team()
        options = [SelectOptionDict(value=club.id, label=club_label(club)) for club in self._found]
        return self.async_show_form(
            step_id="club",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CLUB_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST
                            if len(options) <= LIST_MODE_MAX
                            else SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_team(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the team."""
        assert self._club is not None
        if user_input is not None:
            team = next(t for t in self._teams if t.id == user_input[CONF_TEAM_ID])
            return self._add(team)
        return self.async_show_form(
            step_id="team",
            data_schema=vol.Schema({vol.Required(CONF_TEAM_ID): _team_selector(self._teams)}),
            description_placeholders={"club": self._club.name},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a team's settings: meet time and the weather entity for the forecast."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {
                **subentry.data,
                CONF_MEET_MINUTES_HOME: int(user_input[CONF_MEET_MINUTES_HOME]),
                CONF_MEET_MINUTES_AWAY: int(user_input[CONF_MEET_MINUTES_AWAY]),
            }
            data.pop(CONF_WEATHER_ENTITY, None)
            if weather := user_input.get(CONF_WEATHER_ENTITY):
                data[CONF_WEATHER_ENTITY] = weather
            return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        current = subentry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MEET_MINUTES_HOME,
                    default=current.get(CONF_MEET_MINUTES_HOME, DEFAULT_MEET_MINUTES),
                ): MEET_MINUTES_SELECTOR,
                vol.Required(
                    CONF_MEET_MINUTES_AWAY,
                    default=current.get(CONF_MEET_MINUTES_AWAY, DEFAULT_MEET_MINUTES),
                ): MEET_MINUTES_SELECTOR,
                vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="weather")
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_WEATHER_ENTITY: current.get(CONF_WEATHER_ENTITY)}
            ),
            description_placeholders={"team": subentry.title},
        )
