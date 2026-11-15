"""Base entity: one device per team."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CLUB_ID, CONF_CLUB_NAME, CONF_SPORT, CONF_TEAM_ID, DOMAIN
from .coordinator import KnvbClubCoordinator, KnvbTeamCoordinator


class KnvbEntity(CoordinatorEntity[KnvbTeamCoordinator]):
    """An entity of a team."""

    _attr_has_entity_name = True
    _attr_attribution = "Data: KNVB, voetbal.nl"

    def __init__(self, coordinator: KnvbTeamCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        subentry = coordinator.subentry
        team_id = subentry.data[CONF_TEAM_ID]
        self._attr_unique_id = f"{team_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, team_id)},
            name=subentry.title,
            manufacturer=subentry.data.get(CONF_CLUB_NAME),
            model=subentry.data.get(CONF_SPORT),
            entry_type=DeviceEntryType.SERVICE,
        )


class KnvbClubEntity(CoordinatorEntity[KnvbClubCoordinator]):
    """An entity of a followed club (all its teams)."""

    _attr_has_entity_name = True
    _attr_attribution = "Data: KNVB, voetbal.nl"

    def __init__(self, coordinator: KnvbClubCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        subentry = coordinator.subentry
        club_id = subentry.data[CONF_CLUB_ID]
        self._attr_unique_id = f"club_{club_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"club_{club_id}")},
            name=subentry.title,
            model="Club",
            entry_type=DeviceEntryType.SERVICE,
        )
