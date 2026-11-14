"""Tests for the MijnKNLTB client: cookie wall, login, logging in again, errors."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tenniscompetitie.api import (
    KnltbAuthError,
    KnltbClient,
    KnltbConnectionError,
    KnltbResponseError,
    player_path,
)

from .conftest import (
    LOGIN,
    OWN_ID,
    PASSWORD,
    URL_COOKIEWALL,
    URL_LOGIN,
    URL_LOGIN_FORM,
    URL_PROFILE,
    URL_SEARCH,
    calls_to,
    load_fixture,
    mock_login,
    page_at,
)


def _client(hass: HomeAssistant) -> KnltbClient:
    return KnltbClient(async_get_clientsession(hass), LOGIN, PASSWORD)


async def test_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Only necessary cookies, then the form with its token; finds the own profile."""
    mock_login(aioclient_mock)
    client = _client(hass)
    await client.login()
    assert client.own_profile_id == OWN_ID

    _method, _url, data, _headers = aioclient_mock.mock_calls[0]
    assert ("CookiePurposes", "1") in data
    assert ("CookiePurposes", "2") in data
    assert not any(key == "CookiePurposes" and value not in ("1", "2") for key, value in data)
    _method, _url, data, _headers = aioclient_mock.mock_calls[2]
    assert dict(data) == {
        "Login": LOGIN,
        "Password": PASSWORD,
        "ReturnUrl": "/",
        "__RequestVerificationToken": "test-token-0123456789",
    }


async def test_wrong_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The login form shown again means the login was refused."""
    mock_login(aioclient_mock, ok=False)
    with pytest.raises(KnltbAuthError):
        await _client(hass).login()


async def test_login_form_gone(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """No login form at all: the site changed."""
    aioclient_mock.post(URL_COOKIEWALL, text="")
    aioclient_mock.get(URL_LOGIN_FORM, text="<html><body>Onderhoud</body></html>")
    with pytest.raises(KnltbResponseError):
        await _client(hass).login()


async def test_logs_in_when_the_session_ended(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A page that lands on the login page logs in and asks again."""
    mock_login(aioclient_mock)
    answers = iter(
        [
            page_at(URL_LOGIN_FORM.split("?")[0], "login.html"),
            page_at(URL_PROFILE, "profile.html"),
        ]
    )

    async def profile(*_args):
        return next(answers)

    aioclient_mock.get(URL_PROFILE, side_effect=profile)
    client = _client(hass)
    html = await client.get(player_path(OWN_ID))
    assert "Sam de Vries" in html
    assert calls_to(aioclient_mock, URL_LOGIN) == 1
    assert calls_to(aioclient_mock, URL_PROFILE) == 2


async def test_still_not_logged_in(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Logging in again that does not help is an authentication error."""
    mock_login(aioclient_mock)
    aioclient_mock.get(URL_PROFILE, text=load_fixture("login.html"))
    with pytest.raises(KnltbAuthError):
        await _client(hass).get(player_path(OWN_ID))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (HTTPStatus.INTERNAL_SERVER_ERROR, KnltbConnectionError),
        (HTTPStatus.SERVICE_UNAVAILABLE, KnltbConnectionError),
        (HTTPStatus.NOT_FOUND, KnltbResponseError),
    ],
)
async def test_http_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, error: type
) -> None:
    """Server errors are connection problems; other errors unexpected answers."""
    aioclient_mock.get(URL_PROFILE, status=status, text="")
    with pytest.raises(error):
        await _client(hass).get(player_path(OWN_ID))


async def test_timeout(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A timeout is a connection problem."""
    aioclient_mock.get(URL_PROFILE, exc=TimeoutError())
    with pytest.raises(KnltbConnectionError):
        await _client(hass).get(player_path(OWN_ID))


async def test_search(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The search query is URL-encoded."""
    aioclient_mock.get(f"{URL_SEARCH}?q=de+vries", text=load_fixture("search.html"))
    aioclient_mock.get(f"{URL_SEARCH}?q=de%20vries", text=load_fixture("search.html"))
    players = await _client(hass).search_players("de vries")
    assert len(players) == 3


async def test_own_profile_from_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a login yet, the own profile comes from the home page."""
    aioclient_mock.get("https://mijnknltb.toernooi.nl/", text=load_fixture("home.html"))
    assert await _client(hass).get_own_profile_id() == OWN_ID


def test_close_detaches_the_session() -> None:
    """Home Assistant's sessions share a connection pool: detach, never close."""
    session = MagicMock()
    KnltbClient(session, LOGIN, PASSWORD).close()
    session.detach.assert_called_once_with()
    session.close.assert_not_called()
