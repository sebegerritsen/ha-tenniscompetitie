"""Tests for setting up an account: sensors, calendar, diagnostics, errors and repairs."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tenniscompetitie.calendar import player_events
from custom_components.tenniscompetitie.const import (
    CONF_CLUB,
    CONF_NAME,
    CONF_PROFILE_ID,
    DOMAIN,
    ISSUE_MARKUP_CHANGED,
    SUBENTRY_PLAYER,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_MATCH_DAY,
)
from custom_components.tenniscompetitie.diagnostics import async_get_config_entry_diagnostics
from custom_components.tenniscompetitie.models import NL_TZ, Discipline
from custom_components.tenniscompetitie.sensor import history_rows

from .conftest import (
    NOW,
    OTHER_ID,
    OWN_ID,
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

PREFIX = "sensor.sam_de_vries"


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> MockConfigEntry:
    """Set up the account at the test's 'now'."""
    freezer.move_to(NOW)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


async def test_rating_sensors(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """State = DSS rating; strength, next strength and season figures as attributes."""
    state = hass.states.get(f"{PREFIX}_singles_rating")
    assert state is not None
    assert float(state.state) == pytest.approx(6.1234)
    assert state.attributes["playing_strength"] == 6
    assert state.attributes["next_playing_strength"] == 5
    assert state.attributes["next_from"] == "2027-01-01"
    assert state.attributes["trend"] == "up"
    assert state.attributes["season"] == "Seizoen 2026 - 2027"
    assert state.attributes["start_rating"] == pytest.approx(6.2001)
    assert state.attributes["rated_matches"] == 4
    assert state.attributes["state_class"] == "measurement"
    assert float(hass.states.get(f"{PREFIX}_doubles_rating").state) == pytest.approx(6.8765)
    padel = hass.states.get(f"{PREFIX}_padel_rating")
    assert float(padel.state) == pytest.approx(8.0)
    assert padel.attributes["trend"] == "down"


async def test_results_sensors(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Win-loss this year, the last result and the last match."""
    won = hass.states.get(f"{PREFIX}_matches_won_this_year")
    assert won.state == "30"
    assert won.attributes["win_percentage"] == 60
    assert won.attributes["career"] == 120
    assert (won.attributes["singles"], won.attributes["doubles"], won.attributes["mixed"]) == (
        18,
        10,
        2,
    )
    lost = hass.states.get(f"{PREFIX}_matches_lost_this_year")
    assert lost.state == "20"
    assert lost.attributes["career"] == 80

    last_result = hass.states.get(f"{PREFIX}_last_result")
    assert last_result.state == "lost"
    assert last_result.attributes["last_five"] == ["won", "lost", "won", "won", "lost"]

    last_match = hass.states.get(f"{PREFIX}_last_match")
    assert last_match.state == "2026-11-12"
    assert last_match.attributes["event"] == "Winter Dubbeltoernooi 2026"
    assert last_match.attributes["result"] == "won"
    assert last_match.attributes["partners"] == ["Kim Smit"]
    assert last_match.attributes["opponents"] == ["Lee de Boer", "Noa Visser"]
    assert last_match.attributes["score"] == "6-3 6-2"


async def test_next_match(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The next match: today's planned tournament match comes before tomorrow's team match."""
    state = hass.states.get(f"{PREFIX}_next_match")
    assert datetime.fromisoformat(state.state) == datetime.fromisoformat(
        "2026-11-14T14:00:00+00:00"
    )
    assert state.attributes["kind"] == "tournament"
    assert state.attributes["round"] == "Kwartfinale"


async def test_competition_and_ranking(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Position of the first team of the newest competition, and the first ranking."""
    state = hass.states.get(f"{PREFIX}_competition_position")
    assert state.state == "5"
    assert state.attributes["competition"] == "KNLTB najaar Tennis 2026"
    assert state.attributes["team"] == "VOORBEELD 1"
    assert state.attributes["points"] == 14
    assert [team["position"] for team in state.attributes["teams"]] == [5, 3]
    assert (state.attributes["wins"], state.attributes["losses"]) == (7, 3)

    ranking = hass.states.get(f"{PREFIX}_ranking")
    assert ranking.state == "150"
    assert ranking.attributes["category"] == "Jongens t/m 16 jaar"
    assert len(ranking.attributes["other_rankings"]) == 2


async def test_calendar(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Team matches (timed) and tournament matches (all day, or timed when known)."""
    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    events = player_events(coordinator.data)
    summaries = [event.summary for event in events]
    assert "VOORBEELD 1 – TESTCLUB 1 (5–1)" in summaries
    assert "TESTCLUB 2 – VOORBEELD 1" in summaries
    team = next(event for event in events if event.summary.startswith("VOORBEELD 1 – OEFENBAAN"))
    assert team.start == datetime.fromisoformat("2026-11-15T11:30:00+01:00")
    assert team.description == (
        "KNLTB najaar Tennis 2026\n"
        "Jongens 13 t/m 17 jaar Zondag – Hoofdklasse – Afdeling 4\n"
        "Ronde 6"
    )
    planned = next(event for event in events if "Kwartfinale" in event.summary)
    assert planned.summary == "Winter Dubbeltoernooi 2026 – Kwartfinale: Jo Mulder / Ries Bos"
    assert planned.location == "De Proefbaan"
    all_day = next(event for event in events if "Halve finale" in event.summary)
    assert all_day.all_day

    state = hass.states.get("calendar.sam_de_vries_matches")
    assert state.attributes["message"] == planned.summary


async def test_device(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """One device per player, with the club as model."""
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), loaded.entry_id)
    assert device.identifiers == {(DOMAIN, OWN_ID)}
    assert device.name == "Sam de Vries"
    assert device.model == "T.V. Voorbeeld"


async def test_match_day_polling(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """On a day with a match the player is polled more often."""
    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    assert coordinator.update_interval == UPDATE_INTERVAL_MATCH_DAY


async def test_idle_polling(
    hass: HomeAssistant,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> None:
    """Without a match today or tomorrow the player is polled every few hours."""
    freezer.move_to("2026-12-09T08:00:00+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    assert coordinator.update_interval == UPDATE_INTERVAL


async def test_diagnostics(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Diagnostics show what was read, never names, numbers or profile ids."""
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded)
    text = json.dumps(diagnostics)
    for secret in ("Sam de Vries", "99990001", OWN_ID, "geheim", "testspeler", "Kim Smit"):
        assert secret not in text
    player = diagnostics["players"]["player_1"]["data"]
    assert player["ratings"] == ["doubles", "padel", "singles"]
    assert player["competitions"] == 2
    assert player["team_pages"][0]["matches"] == 4
    assert player["parse_failures"] == []


async def test_wrong_password_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A refused login asks the user to log in again."""
    mock_login(aioclient_mock, ok=False)
    aioclient_mock.get(URL_PROFILE, text=load_fixture("login.html"))
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_profile_unreadable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A profile page that cannot be read retries setup and shows the repair."""
    mock_login(aioclient_mock)
    aioclient_mock.get(URL_PROFILE, text="<html><a href='/user/LogOff'>x</a></html>")
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MARKUP_CHANGED)
    assert issue is not None
    assert issue.translation_placeholders == {"pages": "profile"}


async def test_rating_page_unreadable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> None:
    """A rating page that cannot be read leaves the rest working, with a repair."""
    freezer.move_to(NOW)
    mock_login(aioclient_mock)
    mock_entries(aioclient_mock)
    aioclient_mock.get(URL_RATING, text=load_fixture("profile.html").replace("Enkel", "X"))
    aioclient_mock.get(URL_LEAGUES, text=load_fixture("leagues_empty.html"))
    aioclient_mock.get(f"{URL_LEAGUES}/2025", text=load_fixture("leagues_empty.html"))
    aioclient_mock.get(URL_PROFILE, text=load_fixture("profile.html"))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(f"{PREFIX}_singles_rating")
    assert float(state.state) == pytest.approx(6.1234)
    assert state.attributes["start_rating"] is None
    assert hass.states.get(f"{PREFIX}_competition_position").state == "unknown"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MARKUP_CHANGED)
    assert issue.translation_placeholders == {"pages": "rating"}

    # Fixed: the repair goes away with the next update.
    aioclient_mock.clear_requests()
    mock_login(aioclient_mock)
    mock_entries(aioclient_mock)
    aioclient_mock.get(URL_RATING, text=load_fixture("rating.html"))
    aioclient_mock.get(URL_LEAGUES, text=load_fixture("leagues_empty.html"))
    aioclient_mock.get(f"{URL_LEAGUES}/2025", text=load_fixture("leagues_empty.html"))
    aioclient_mock.get(URL_PROFILE, text=load_fixture("profile.html"))
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MARKUP_CHANGED) is None


async def test_last_year_competitions(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> None:
    """Without competitions this year, last year's are shown."""
    freezer.move_to("2027-01-20T08:00:00+00:00")
    mock_login(aioclient_mock)
    mock_entries(aioclient_mock)
    aioclient_mock.get(URL_RATING, text=load_fixture("rating.html"))
    aioclient_mock.get(f"{URL_LEAGUES}/2026", text=load_fixture("leagues.html"))
    aioclient_mock.get(URL_LEAGUES, text=load_fixture("leagues_empty.html"))
    aioclient_mock.get(URL_PROFILE, text=load_fixture("profile.html"))
    aioclient_mock.get(
        "https://mijnknltb.toernooi.nl/league/00000000-0000-4000-8000-0000000000a1/team/1187",
        text=load_fixture("team.html"),
    )
    aioclient_mock.get(
        "https://mijnknltb.toernooi.nl/league/00000000-0000-4000-8000-0000000000a1/team/353",
        text=load_fixture("team_empty.html"),
    )
    aioclient_mock.get(
        "https://mijnknltb.toernooi.nl/league/00000000-0000-4000-8000-0000000000a1/player/6001",
        text=load_fixture("league_player.html"),
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(f"{PREFIX}_competition_position").state == "5"


async def test_adding_a_player_reloads(
    hass: HomeAssistant, loaded: MockConfigEntry, mock_site: AiohttpClientMocker
) -> None:
    """A new player subentry reloads the account with a coordinator for it."""
    other = f"https://mijnknltb.toernooi.nl/player-profile/{OTHER_ID}"
    mock_site.get(other, text=load_fixture("profile.html"))
    mock_site.get(f"{other}/Rating", text=load_fixture("rating.html"))
    mock_site.get(f"{other}/leagues", text=load_fixture("leagues.html"))
    hass.config_entries.async_add_subentry(
        loaded,
        ConfigSubentry(
            data={CONF_PROFILE_ID: OTHER_ID, CONF_NAME: "Robin de Vries", CONF_CLUB: None},
            subentry_type=SUBENTRY_PLAYER,
            title="Robin de Vries",
            unique_id=OTHER_ID,
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded.state is ConfigEntryState.LOADED
    assert len(loaded.runtime_data.coordinators) == 2
    assert hass.states.get("sensor.robin_de_vries_singles_rating") is not None


async def test_unload(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The account unloads."""
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.NOT_LOADED


async def test_entries_and_planned_matches(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The account's own player gets its entries and planned matches of entered tournaments."""
    entries = hass.states.get(f"{PREFIX}_tournament_entries")
    assert entries.state == "2"  # the running tournament and the pending Junior Tour week
    assert [entry["tournament"] for entry in entries.attributes["entries"]] == [
        "Najaarstoernooi Teststad 2026",
        "JT Voorbeeldpark Brons t/m 16",
    ]
    assert entries.attributes["entries"][1]["priority"] == 1

    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    events = player_events(coordinator.data, date(2026, 11, 14))
    planned = next(
        event for event in events if "Ronde 1" in event.summary and "Teststad" in event.summary
    )
    assert planned.start == datetime.fromisoformat("2026-11-14T20:15:00+01:00")
    assert planned.location == "TV Teststad"
    # The Junior Tour week has no matches yet: one all-day block over its days.
    block = next(event for event in events if event.summary.startswith("JT Voorbeeldpark"))
    assert block.summary == "JT Voorbeeldpark Brons t/m 16 (Tennis JE 16 Brons)"
    assert (block.start, block.end) == (date(2026, 12, 5), date(2026, 12, 7))
    assert block.description == "Junioren Tour Brons\nVoorkeur 1"
    # The running tournament has its matches, so no block for it.
    assert not any(event.summary.startswith("Najaarstoernooi Teststad 2026 (") for event in events)


async def test_entries_only_for_own_player(
    hass: HomeAssistant, loaded: MockConfigEntry, mock_site: AiohttpClientMocker
) -> None:
    """Another followed player has no entries sensor (only the account holder's are visible)."""
    other = f"https://mijnknltb.toernooi.nl/player-profile/{OTHER_ID}"
    mock_site.get(other, text=load_fixture("profile.html"))
    mock_site.get(f"{other}/Rating", text=load_fixture("rating.html"))
    mock_site.get(f"{other}/leagues", text=load_fixture("leagues.html"))
    hass.config_entries.async_add_subentry(
        loaded,
        ConfigSubentry(
            data={CONF_PROFILE_ID: OTHER_ID, CONF_NAME: "Robin de Vries", CONF_CLUB: None},
            subentry_type=SUBENTRY_PLAYER,
            title="Robin de Vries",
            unique_id=OTHER_ID,
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(f"{PREFIX}_tournament_entries") is not None
    assert hass.states.get("sensor.robin_de_vries_tournament_entries") is None


async def test_pool_standing_and_venue(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """The first team's pool standing, and the venue of its next match in the calendar."""
    state = hass.states.get(f"{PREFIX}_competition_position")
    standing = state.attributes["standing"]
    assert [row["team"] for row in standing] == ["PROEFPARK 1", "TESTCLUB 1", "VOORBEELD 1"]
    assert standing[2]["own_team"] is True
    assert standing[2]["form"] == ["won", "lost", "lost", "lost", "won"]
    assert state.attributes["teams"][1]["standing"][0]["team"] == "PROEFPARK 1"

    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    events = player_events(coordinator.data, date(2026, 11, 14))
    home = next(e for e in events if e.summary.startswith("VOORBEELD 1 – OEFENBAAN"))
    assert home.location == "T.V. Voorbeeld, Voorbeeldlaan 1, 1234 AB Voorbeeldstad"


async def test_match_today(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """On on a day with matches; the first one by start time in the attributes."""
    state = hass.states.get("binary_sensor.sam_de_vries_match_today")
    assert state.state == "on"
    assert state.attributes["matches"] == 2
    assert state.attributes["round"] == "Kwartfinale"
    assert state.attributes["start"] == "2026-11-14T15:00:00+01:00"


async def test_next_team_match_line_up(
    hass: HomeAssistant,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> None:
    """The next team match: venue, home or away, the line-up and the player's own matches."""
    freezer.move_to("2026-11-15T07:00:00+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(f"{PREFIX}_next_match")
    assert state.attributes["kind"] == "competition"
    assert state.attributes["home_away"] == "home"
    assert state.attributes["venue"] == "T.V. Voorbeeld"
    assert state.attributes["address"] == "Voorbeeldlaan 1, 1234 AB Voorbeeldstad"
    assert state.attributes["latitude"] == pytest.approx(52.00001)
    assert state.attributes["plays"] == ["HE2", "HD1"]
    assert state.attributes["line_up"][0] == {
        "match": "HE1",
        "players": ["Max Kok"],
        "opponents": ["Wim Wolters"],
    }


async def test_rating_history(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Past seasons are read in the background: the history and the seasons' figures."""
    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    history = coordinator.data.rating_history[Discipline.SINGLES]
    assert history == [
        (date(2025, 9, 7), pytest.approx(6.5)),
        (date(2025, 9, 13), pytest.approx(6.55)),
        (date(2025, 9, 20), pytest.approx(6.4)),
        (date(2025, 9, 27), pytest.approx(6.35)),
    ]
    seasons = hass.states.get(f"{PREFIX}_singles_rating").attributes["seasons"]
    assert seasons[0]["season"] == "Seizoen 2026 - 2027"
    assert seasons[0]["end_rating"] == pytest.approx(6.1234)
    assert {s["season"] for s in seasons} >= {"Seizoen 2025 - 2026", "Seizoen 2024 - 2025"}
    assert next(s for s in seasons if s["season"] == "Seizoen 2025 - 2026")["end_rating"] == (
        pytest.approx(6.4)
    )


def test_history_rows() -> None:
    """Rated matches become hourly rows at noon, an hour apart on the same day."""
    rows = history_rows(
        [(date(2025, 9, 7), 6.5), (date(2025, 9, 7), 6.4), (date(2025, 9, 8), 6.3)],
        datetime(2025, 9, 8, 0, 0, tzinfo=NL_TZ),
    )
    assert rows == [
        (datetime(2025, 9, 7, 12, 0, tzinfo=NL_TZ), 6.5),
        (datetime(2025, 9, 7, 13, 0, tzinfo=NL_TZ), 6.4),
    ]


async def test_match_update_event(
    hass: HomeAssistant, loaded: MockConfigEntry, mock_site: AiohttpClientMocker
) -> None:
    """A planned match that moves fires the Match update event with both times."""
    # The entity starts reporting once Home Assistant runs (on the next tick).
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass.states.get("event.sam_de_vries_match_update").state == "unknown"
    moved = load_fixture("tournament_player.html").replace(
        "za 14-11-2026 20:15", "za 14-11-2026 21:00"
    )
    mock_site.clear_requests()
    mock_login(mock_site)
    mock_site.get(URL_TOURNAMENT_PLAYER, text=moved)
    mock_entries(mock_site)
    mock_site.get(URL_RATING, text=load_fixture("rating.html"))
    mock_site.get(URL_LEAGUES, text=load_fixture("leagues.html"))
    mock_site.get(URL_PROFILE, text=load_fixture("profile.html"))
    mock_site.get(URL_TEAM_1, text=load_fixture("team.html"))
    mock_site.get(URL_TEAM_2, text=load_fixture("team_empty.html"))
    mock_site.get(URL_LEAGUE_PLAYER, text=load_fixture("league_player.html"))
    coordinator = next(iter(loaded.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("event.sam_de_vries_match_update")
    assert state.attributes["event_type"] == "rescheduled"
    assert state.attributes["start"] == "2026-11-14T21:00:00+01:00"
    assert state.attributes["previous_start"] == "2026-11-14T20:15:00+01:00"
    assert state.attributes["opponents"] == ["Bram Dekker [2]", "Fien Dekker"]


async def test_actions(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """get_matches (all or upcoming) and get_standings for a player device."""
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), loaded.entry_id)
    response = await hass.services.async_call(
        DOMAIN,
        "get_matches",
        {"device_id": device.id, "upcoming_only": True},
        blocking=True,
        return_response=True,
    )
    upcoming = response["matches"]
    assert [m.get("round") for m in upcoming][:3] == ["Kwartfinale", "Ronde 1", "Ronde 6"]
    assert upcoming[2]["home"] == "VOORBEELD 1"
    assert upcoming[2]["venue"] == "T.V. Voorbeeld"
    everything = await hass.services.async_call(
        DOMAIN, "get_matches", {"device_id": device.id}, blocking=True, return_response=True
    )
    assert len(everything["matches"]) > len(upcoming)

    standings = await hass.services.async_call(
        DOMAIN, "get_standings", {"device_id": device.id}, blocking=True, return_response=True
    )
    first = standings["teams"][0]
    assert (first["team"], first["position"], first["points"]) == ("VOORBEELD 1", 5, 14)
    assert len(first["standing"]) == 3

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_matches", {"device_id": "nope"}, blocking=True, return_response=True
        )


async def test_junior_tour_matches(
    hass: HomeAssistant,
    mock_site: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    dutch_time: None,
) -> None:
    """A Junior Tour entry: its event is found by name on the tournament's events page."""
    freezer.move_to("2026-11-28T08:00:00+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = next(iter(config_entry.runtime_data.coordinators.values()))
    events = player_events(coordinator.data, date(2026, 11, 28))
    planned = next(e for e in events if "JT Voorbeeldpark" in e.summary)
    assert planned.summary == "JT Voorbeeldpark Brons t/m 16 – Ronde 1: Bo Bakker"
    assert planned.start == datetime.fromisoformat("2026-12-05T09:30:00+01:00")
    assert planned.location == "Voorbeeldpark - Baan 4"
    assert hass.states.get(f"{PREFIX}_next_match").attributes["draw"] == "Tennis JE 16 Brons"
