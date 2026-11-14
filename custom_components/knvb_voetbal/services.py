"""Actions: get a team's or club's matches, or a team's standing, as a response."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .calendar import score_text
from .const import DOMAIN
from .coordinator import KnvbClubCoordinator, KnvbTeamCoordinator, match_result

SERVICE_GET_MATCHES = "get_matches"
SERVICE_GET_STANDING = "get_standing"
ATTR_DEVICE_ID = "device_id"
ATTR_UPCOMING_ONLY = "upcoming_only"

GET_MATCHES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_UPCOMING_ONLY, default=False): cv.boolean,
    }
)
GET_STANDING_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})


def _owned_by(device: dr.DeviceEntry, entry_id: str) -> bool:
    """Return whether a device belongs to a config entry (old and new device registry)."""
    if (owner := getattr(device, "config_entry_id", None)) is not None:
        return bool(owner == entry_id)
    return entry_id in device.config_entries


def _coordinator(hass: HomeAssistant, device_id: str) -> KnvbTeamCoordinator | KnvbClubCoordinator:
    """Return the coordinator of a team or club device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_device")
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED or not _owned_by(device, entry.entry_id):
            continue
        runtime = entry.runtime_data
        for team in runtime.coordinators.values():
            if (DOMAIN, team.team_id) in device.identifiers:
                return team
        for club in runtime.clubs.values():
            if (DOMAIN, f"club_{club.club_id}") in device.identifiers:
                return club
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_device")


async def _get_matches(call: ServiceCall) -> ServiceResponse:
    # Imported here: sensor imports this module's siblings, keep the import graph flat.
    from .sensor import club_match_item, match_attributes  # noqa: PLC0415

    coordinator = _coordinator(call.hass, call.data[ATTR_DEVICE_ID])
    upcoming_only = call.data[ATTR_UPCOMING_ONLY]
    now = dt_util.utcnow()
    matches: list[dict[str, Any]] = []
    if isinstance(coordinator, KnvbTeamCoordinator):
        data = coordinator.data
        source = data.upcoming(now) if upcoming_only else data.matches
        for match in source:
            item = match_attributes(data, match)
            item.pop("opponent_logo", None)
            item["score"] = score_text(match) if match.has_result else None
            item["result"] = match_result(match, data.team_id) if match.has_result else None
            matches.append(item)
    else:
        club = coordinator.data
        for match in club.matches:
            if upcoming_only and (match.has_result or match.start is None or match.start < now):
                continue
            matches.append(club_match_item(club, match))
    return {"matches": matches}


async def _get_standing(call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(call.hass, call.data[ATTR_DEVICE_ID])
    if not isinstance(coordinator, KnvbTeamCoordinator):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_team")
    data = coordinator.data
    return {
        "competition": data.pool.label if data.pool else None,
        "position": data.standing.position if data.standing else None,
        "table": [
            {
                "position": row.position,
                "team": row.team.name,
                "played": row.played,
                "won": row.won,
                "drawn": row.drawn,
                "lost": row.lost,
                "goals_for": row.goals_for,
                "goals_against": row.goals_against,
                "goal_difference": row.goal_difference,
                "points": row.points,
                "penalty_points": row.penalty_points,
                "form": data.form(row.team.id),
            }
            for row in data.standings
        ],
        "periods": data.period_positions(),
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MATCHES,
        _get_matches,
        schema=GET_MATCHES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_STANDING,
        _get_standing,
        schema=GET_STANDING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
