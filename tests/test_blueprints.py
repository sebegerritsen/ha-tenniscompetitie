"""Run the blueprints as automations, with a test event in place of the phone notification."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import (
    URL_LEAGUE_PLAYER,
    URL_LEAGUES,
    URL_PROFILE,
    URL_RATING,
    URL_TEAM_1,
    URL_TEAM_2,
    URL_TOURNAMENT_PLAYER,
    load_fixture,
    mock_entries,
    mock_login,
)

BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation" / "tenniscompetitie"
NOTIFY = "    domain: mobile_app\n    type: notify\n"
EVENT = "tennis_test_notify"

pytestmark = pytest.mark.usefixtures("dutch_time")


def _install(hass: HomeAssistant, tmp_path: Path, name: str) -> None:
    """Copy a blueprint into the config dir, its notification replaced by an event."""
    text = (BLUEPRINTS / name).read_text(encoding="utf-8")
    head, _, tail = text.partition("  - device_id: !input notify_device\n" + NOTIFY)
    assert tail, f"{name} has no phone notification as its last action"
    text = (
        head
        + f"  - event: {EVENT}\n    event_data:\n"
        + "".join(f"  {line}\n" if line else "\n" for line in tail.splitlines())
    )
    target = tmp_path / "blueprints" / "automation" / "tenniscompetitie" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    hass.config.config_dir = str(tmp_path)


async def _automation(hass: HomeAssistant, name: str, inputs: dict[str, Any]) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"tenniscompetitie/{name}",
                    "input": {"notify_device": "phone", **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _mock_moved_match(mock: AiohttpClientMocker) -> None:
    """Answer the pages again, with the planned tournament match moved to 21:00."""
    mock.clear_requests()
    mock_login(mock)
    moved = load_fixture("tournament_player.html").replace(
        "za 14-11-2026 20:15", "za 14-11-2026 21:00"
    )
    mock.get(URL_TOURNAMENT_PLAYER, text=moved)
    mock_entries(mock)
    mock.get(URL_RATING, text=load_fixture("rating.html"))
    mock.get(URL_LEAGUES, text=load_fixture("leagues.html"))
    mock.get(URL_PROFILE, text=load_fixture("profile.html"))
    mock.get(URL_TEAM_1, text=load_fixture("team.html"))
    mock.get(URL_TEAM_2, text=load_fixture("team_empty.html"))
    mock.get(URL_LEAGUE_PLAYER, text=load_fixture("league_player.html"))


async def test_match_update_notify(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A moved match gives a readable notification, in Dutch."""
    freezer.move_to("2026-11-14T08:00:00+00:00")
    await _setup(hass, config_entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    _install(hass, tmp_path, "match_update_notify.yaml")
    await _automation(
        hass,
        "match_update_notify.yaml",
        {"update_entity": "event.sam_de_vries_match_update", "language": "nl"},
    )
    events = async_capture_events(hass, EVENT)

    _mock_moved_match(mock_site)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(events) == 1
    assert events[0].data["title"] == "Sam de Vries · Verplaatst"
    assert events[0].data["message"] == (
        "za 14-11 21:00 (was za 14-11 20:15) · Bram Dekker [2] / Fien Dekker · TV Teststad"
    )


async def test_match_update_notify_skips_unwanted(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Kinds of updates that were not chosen send nothing."""
    freezer.move_to("2026-11-14T08:00:00+00:00")
    await _setup(hass, config_entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    _install(hass, tmp_path, "match_update_notify.yaml")
    await _automation(
        hass,
        "match_update_notify.yaml",
        {"update_entity": "event.sam_de_vries_match_update", "event_types": ["result"]},
    )
    events = async_capture_events(hass, EVENT)
    _mock_moved_match(mock_site)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert events == []


async def test_match_reminder(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The reminder comes the set time before the next match, with its details."""
    freezer.move_to("2026-11-14T07:00:00+00:00")  # 08:00; the next match is at 15:00
    await _setup(hass, config_entry)
    _install(hass, tmp_path, "match_reminder.yaml")
    await _automation(
        hass,
        "match_reminder.yaml",
        {"next_match_entity": "sensor.sam_de_vries_next_match", "lead_time": 360},
    )
    events = async_capture_events(hass, EVENT)

    freezer.move_to("2026-11-14T08:01:00+00:00")  # 09:01: within six hours of 15:00
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["title"] == "Sam de Vries · 15:00: Jo Mulder / Ries Bos"
    assert events[0].data["message"] == (
        "Winter Dubbeltoernooi 2026 · Kwartfinale · Tennis GD 13-17 · met Kim Smit · De Proefbaan"
    )


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_match_day_summary(
    hass: HomeAssistant,
    tmp_path: Path,
    freezer: FrozenDateTimeFactory,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """At the set time on a match day: today's matches, one per line."""
    freezer.move_to("2026-11-14T07:59:00+00:00")  # 08:59
    await _setup(hass, config_entry)
    _install(hass, tmp_path, "match_day_summary.yaml")
    await _automation(
        hass,
        "match_day_summary.yaml",
        {"match_today_entity": "binary_sensor.sam_de_vries_match_today", "time": "09:00:00"},
    )
    events = async_capture_events(hass, EVENT)

    freezer.move_to("2026-11-14T08:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["title"] == "Sam de Vries · vandaag tennis"
    assert events[0].data["message"] == (
        "15:00 · Jo Mulder / Ries Bos · De Proefbaan\n"
        "20:15 · Bram Dekker [2] / Fien Dekker · TV Teststad"
    )
