"""The Tenniscompetitie integration: ratings, results and matches of KNLTB players."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import KnltbClient
from .const import CONF_LOGIN, DOMAIN, REQUEST_SPACING, STORAGE_VERSION, SUBENTRY_PLAYER
from .coordinator import (
    KnltbConfigEntry,
    KnltbPlayerCoordinator,
    KnltbRuntimeData,
    update_markup_issue,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


def create_client(hass: HomeAssistant, login: str, password: str) -> KnltbClient:
    """Return a client with a session (and cookie jar) of its own."""
    session = async_create_clientsession(hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
    return KnltbClient(session, login, password, spacing=REQUEST_SPACING)


async def async_setup_entry(hass: HomeAssistant, entry: KnltbConfigEntry) -> bool:
    """Set up an account and a coordinator per followed player."""
    client = create_client(hass, entry.data[CONF_LOGIN], entry.data[CONF_PASSWORD])
    entry.async_on_unload(client.close)
    runtime = KnltbRuntimeData(client)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_PLAYER:
            runtime.coordinators[subentry_id] = KnltbPlayerCoordinator(
                hass, entry, subentry, client
            )
    # The client makes one request at a time, so this is sequential in practice.
    await asyncio.gather(
        *(
            coordinator.async_config_entry_first_refresh()
            for coordinator in runtime.coordinators.values()
        )
    )
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    players = _players(entry)

    async def _reload_on_player_change(hass: HomeAssistant, entry: KnltbConfigEntry) -> None:
        if _players(entry) != players:
            for removed in set(players) - set(entry.subentries):
                await Store(
                    hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{removed}"
                ).async_remove()
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_on_player_change))
    return True


def _players(entry: KnltbConfigEntry) -> dict[str, dict[str, Any]]:
    return {key: dict(subentry.data) for key, subentry in entry.subentries.items()}


async def async_unload_entry(hass: HomeAssistant, entry: KnltbConfigEntry) -> bool:
    """Unload an account."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for subentry_id in entry.runtime_data.coordinators:
            update_markup_issue(hass, subentry_id, set())
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: KnltbConfigEntry) -> None:
    """Remove what was kept per player (previous update, rating history)."""
    for subentry_id in entry.subentries:
        await Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{subentry_id}"
        ).async_remove()
