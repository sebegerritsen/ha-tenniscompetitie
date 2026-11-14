"""Fixtures for the Tenniscompetitie tests. Every person, club and number in them is made up."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_PASSWORD
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.tenniscompetitie.api import BASE_URL
from custom_components.tenniscompetitie.const import (
    CONF_CLUB,
    CONF_LOGIN,
    CONF_NAME,
    CONF_PROFILE_ID,
    DOMAIN,
    SUBENTRY_PLAYER,
)

FIXTURES = Path(__file__).parent / "fixtures"

LOGIN = "testspeler"
PASSWORD = "geheim"
OWN_ID = "00000000-0000-4000-8000-000000000001"
OTHER_ID = "00000000-0000-4000-8000-000000000002"
LEAGUE = "00000000-0000-4000-8000-0000000000a1"

# Saturday 14 November 2026, 09:00 in the Netherlands: the team plays tomorrow.
NOW = "2026-11-14T08:00:00+00:00"

URL_COOKIEWALL = f"{BASE_URL}/cookiewall/Save"
URL_LOGIN_FORM = f"{BASE_URL}/user/login?ReturnUrl=%2f"
URL_LOGIN = f"{BASE_URL}/user"
URL_HOME = f"{BASE_URL}/"
URL_PROFILE = f"{BASE_URL}/player-profile/{OWN_ID}"
URL_RATING = f"{URL_PROFILE}/Rating"
URL_LEAGUES = f"{URL_PROFILE}/leagues"
URL_TEAM_1 = f"{BASE_URL}/league/{LEAGUE}/team/1187"
URL_TEAM_2 = f"{BASE_URL}/league/{LEAGUE}/team/353"
URL_LEAGUE_PLAYER = f"{BASE_URL}/league/{LEAGUE}/player/6001"
URL_SEARCH = f"{BASE_URL}/find/player"


def load_fixture(name: str) -> str:
    """Load a saved (anonymised) page."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def page_at(url: str, fixture: str) -> AiohttpClientMockResponse:
    """Return a response that ended up at a URL (as after a redirect)."""
    return AiohttpClientMockResponse("GET", URL(url), text=load_fixture(fixture))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture(autouse=True)
def no_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't space requests out in tests."""
    monkeypatch.setattr("custom_components.tenniscompetitie.REQUEST_SPACING", 0.0)


@pytest.fixture
async def dutch_time(hass: Any) -> None:
    """Run Home Assistant in Dutch time."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")


PLAYER = ConfigSubentryData(
    data={CONF_PROFILE_ID: OWN_ID, CONF_NAME: "Sam de Vries", CONF_CLUB: "T.V. Voorbeeld"},
    subentry_type=SUBENTRY_PLAYER,
    title="Sam de Vries",
    unique_id=OWN_ID,
)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return an account that follows its own player."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sam de Vries",
        unique_id=LOGIN,
        data={CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD},
        subentries_data=[PLAYER],
    )


def mock_login(aioclient_mock: AiohttpClientMocker, *, ok: bool = True) -> None:
    """Answer the cookie wall and the login form."""
    aioclient_mock.post(URL_COOKIEWALL, text=load_fixture("home.html" if ok else "cookiewall.html"))
    aioclient_mock.get(URL_LOGIN_FORM, text=load_fixture("login.html"))
    aioclient_mock.post(URL_LOGIN, text=load_fixture("home.html" if ok else "login_failed.html"))


@pytest.fixture
def mock_site(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer every page the integration reads, as a logged-in member."""
    mock_login(aioclient_mock)
    aioclient_mock.get(URL_HOME, text=load_fixture("home.html"))
    aioclient_mock.get(URL_RATING, text=load_fixture("rating.html"))
    aioclient_mock.get(URL_LEAGUES, text=load_fixture("leagues.html"))
    aioclient_mock.get(URL_PROFILE, text=load_fixture("profile.html"))
    aioclient_mock.get(URL_TEAM_1, text=load_fixture("team.html"))
    aioclient_mock.get(URL_TEAM_2, text=load_fixture("team_empty.html"))
    aioclient_mock.get(URL_LEAGUE_PLAYER, text=load_fixture("league_player.html"))
    aioclient_mock.get(f"{URL_SEARCH}?q=vries", text=load_fixture("search.html"))
    aioclient_mock.get(f"{URL_SEARCH}?q=zzz", text=load_fixture("search_empty.html"))
    return aioclient_mock


def calls_to(mock: AiohttpClientMocker, url: str) -> int:
    """Return how many requests went to a URL."""
    return sum(1 for _method, called, _data, _headers in mock.mock_calls if str(called) == url)
