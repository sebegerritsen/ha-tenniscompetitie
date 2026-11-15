"""Binary sensor per player: is there a match today?"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .coordinator import KnltbConfigEntry, KnltbPlayerCoordinator
from .entity import KnltbEntity
from .models import NL_TZ, Match
from .sensor import match_attributes, team_match_attributes

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnltbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of each player."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnltbMatchToday(coordinator)], config_subentry_id=subentry_id)


class KnltbMatchToday(KnltbEntity, BinarySensorEntity):
    """On all day when the player has a team or tournament match today."""

    _attr_translation_key = "match_today"

    def __init__(self, coordinator: KnltbPlayerCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "match_today")

    async def async_added_to_hass(self) -> None:
        """Also update just after midnight, when the day changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._new_day, hour=0, minute=0, second=1)
        )

    @callback
    def _new_day(self, _now: datetime) -> None:
        self.async_write_ha_state()

    def _today(self) -> list[tuple[datetime | None, Any]]:
        return self.coordinator.data.matches_on(dt_util.now(NL_TZ).date())

    @property
    def is_on(self) -> bool:
        """Return whether the player plays today."""
        return bool(self._today())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return today's first match (by start time) and how many there are."""
        today = self._today()
        if not today:
            return {}
        _start, first = today[0]
        if isinstance(first, Match):
            attributes = match_attributes(first)
        else:
            team, match = first
            attributes = team_match_attributes(
                self.coordinator.data, team.team, team.competition.name, match
            )
        attributes["matches"] = len(today)
        return attributes
