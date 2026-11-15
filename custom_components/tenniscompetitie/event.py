"""Event entity per player: a result, a new or moved match, a new playing strength or rating."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started

from .changes import Change, ChangeType
from .coordinator import KnltbConfigEntry, KnltbPlayerCoordinator, PlayerData
from .entity import KnltbEntity
from .sensor import match_attributes, team_match_attributes

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnltbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the match update entity of each player."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnltbMatchUpdate(coordinator)], config_subentry_id=subentry_id)


def change_attributes(data: PlayerData, change: Change) -> dict[str, Any]:
    """Return the details of a change, for the event."""
    if change.match is not None:
        attributes = match_attributes(change.match)
    elif change.team_match is not None:
        attributes = team_match_attributes(
            data, change.team or "", change.competition, change.team_match
        )
    else:
        attributes = {}
    if change.type is ChangeType.RESCHEDULED:
        attributes["previous_start"] = change.previous.get("start")
    if change.rating is not None:
        rating = change.rating
        attributes.update(
            {
                "discipline": rating.discipline.value,
                "rating": rating.rating,
                "playing_strength": rating.strength,
                "next_playing_strength": rating.next_strength,
                "next_from": rating.next_from.isoformat() if rating.next_from else None,
            }
        )
        if change.type is ChangeType.RATING:
            attributes["previous_rating"] = change.previous.get("rating")
        if change.type is ChangeType.STRENGTH:
            attributes["previous_next_playing_strength"] = change.previous.get("next_strength")
    return attributes


class KnltbMatchUpdate(KnltbEntity, EventEntity):
    """Fires on a result, a newly planned or moved match, a new playing strength or rating."""

    _attr_translation_key = "match_update"

    def __init__(self, coordinator: KnltbPlayerCoordinator) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, "match_update")
        self._attr_event_types = [change.value for change in ChangeType]
        # Changes belong to one update: collect each update's once, fire when ready.
        self._seen: PlayerData | None = None
        self._pending: list[tuple[PlayerData, Change]] = []
        self._ready = False

    async def async_added_to_hass(self) -> None:
        """Report what the first update found (what changed while Home Assistant was off).

        Not right away: state written while the entity is being added is dropped, and
        during start-up automations may not be running yet.
        """
        await super().async_added_to_hass()
        self._collect()
        self.async_on_remove(async_call_later(self.hass, 0, self._async_added))

    @callback
    def _async_added(self, _now: datetime) -> None:
        self.async_on_remove(async_at_started(self.hass, self._async_ready))

    @callback
    def _async_ready(self, _hass: HomeAssistant) -> None:
        self._ready = True
        self._fire()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._collect()
        self._fire()
        super()._handle_coordinator_update()

    @callback
    def _collect(self) -> None:
        data = self.coordinator.data
        if data is None or data is self._seen:
            return
        self._seen = data
        self._pending.extend((data, change) for change in data.changes)

    @callback
    def _fire(self) -> None:
        if not self._ready:
            return
        while self._pending:
            data, change = self._pending.pop(0)
            self._trigger_event(change.type.value, change_attributes(data, change))
            self.async_write_ha_state()
