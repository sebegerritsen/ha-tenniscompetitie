"""Base entity: one device per followed player."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CLUB, DOMAIN
from .coordinator import KnltbPlayerCoordinator


class KnltbEntity(CoordinatorEntity[KnltbPlayerCoordinator]):
    """An entity of a player."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: KnltbPlayerCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        subentry = coordinator.subentry
        profile_id = coordinator.profile_id
        self._attr_unique_id = f"{profile_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, profile_id)},
            name=subentry.title,
            model=subentry.data.get(CONF_CLUB),
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://mijnknltb.toernooi.nl/player-profile/{profile_id}",
        )
