"""Actions: a player's matches, or the standings of the player's teams, as a response."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN
from .coordinator import KnltbPlayerCoordinator

SERVICE_GET_MATCHES = "get_matches"
SERVICE_GET_STANDINGS = "get_standings"
ATTR_DEVICE_ID = "device_id"
ATTR_UPCOMING_ONLY = "upcoming_only"

GET_MATCHES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_UPCOMING_ONLY, default=False): cv.boolean,
    }
)
GET_STANDINGS_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})


def _owned_by(device: dr.DeviceEntry, entry_id: str) -> bool:
    """Return whether a device belongs to a config entry (old and new device registry)."""
    if (owner := getattr(device, "config_entry_id", None)) is not None:
        return bool(owner == entry_id)
    return entry_id in device.config_entries


def _coordinator(hass: HomeAssistant, device_id: str) -> KnltbPlayerCoordinator:
    """Return the coordinator of a player device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_device")
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED or not _owned_by(device, entry.entry_id):
            continue
        for coordinator in entry.runtime_data.coordinators.values():
            if (DOMAIN, coordinator.profile_id) in device.identifiers:
                return coordinator
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_device")


async def _get_matches(call: ServiceCall) -> ServiceResponse:
    # Imported here: sensor imports the coordinator too; keep the import graph flat.
    from .sensor import match_attributes, team_match_attributes  # noqa: PLC0415

    data = _coordinator(call.hass, call.data[ATTR_DEVICE_ID]).data
    upcoming_only = call.data[ATTR_UPCOMING_ONLY]
    now = dt_util.utcnow()
    today = dt_util.now().date()
    items: list[dict[str, Any]] = []
    for match in data.matches:
        if upcoming_only and (match.won is not None or match.day is None or match.day < today):
            continue
        items.append(match_attributes(match))
    for team, match in data.team_matches():
        if upcoming_only and (match.won is not None or match.start is None or match.start < now):
            continue
        items.append(team_match_attributes(data, team.team, team.competition.name, match))
    items.sort(key=lambda item: item.get("start") or item.get("day") or "")
    return {"matches": items}


async def _get_standings(call: ServiceCall) -> ServiceResponse:
    from .sensor import standing_rows  # noqa: PLC0415

    data = _coordinator(call.hass, call.data[ATTR_DEVICE_ID]).data
    return {
        "teams": [
            {
                "competition": team.competition.name,
                "team": team.team,
                "category": team.category,
                "level": team.level,
                "position": team.page.position,
                "points": team.page.points,
                "standing": standing_rows(team),
            }
            for team in data.teams
        ]
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
        SERVICE_GET_STANDINGS,
        _get_standings,
        schema=GET_STANDINGS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
