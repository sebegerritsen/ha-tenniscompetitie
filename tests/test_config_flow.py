"""Tests for the config flow (account) and the subentry flow (players)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tenniscompetitie.config_flow import player_label
from custom_components.tenniscompetitie.const import (
    CONF_CLUB,
    CONF_LOGIN,
    CONF_NAME,
    CONF_PROFILE_ID,
    DOMAIN,
    SUBENTRY_PLAYER,
)
from custom_components.tenniscompetitie.models import PlayerRef

from .conftest import (
    LOGIN,
    OTHER_ID,
    OWN_ID,
    PASSWORD,
    URL_COOKIEWALL,
    URL_LOGIN,
    URL_LOGIN_FORM,
    URL_PROFILE,
    load_fixture,
    mock_login,
)


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_login_and_follow_own_profile(
    hass: HomeAssistant, mock_site: AiohttpClientMocker
) -> None:
    """Log in, then follow the account's own profile."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: f" {LOGIN} ", CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "own_profile"
    assert result["description_placeholders"] == {"name": "Sam de Vries"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"follow_own": True})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sam de Vries"
    assert result["data"] == {CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD}
    entry = result["result"]
    assert entry.unique_id == LOGIN
    (subentry,) = entry.subentries.values()
    assert subentry.subentry_type == SUBENTRY_PLAYER
    assert subentry.unique_id == OWN_ID
    assert subentry.title == "Sam de Vries"
    assert dict(subentry.data) == {
        CONF_PROFILE_ID: OWN_ID,
        CONF_NAME: "Sam de Vries",
        CONF_CLUB: "T.V. Voorbeeld",
    }


async def test_login_without_following(hass: HomeAssistant, mock_site: AiohttpClientMocker) -> None:
    """The own profile can be left out."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"follow_own": False}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].subentries == {}


async def test_account_without_own_profile(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An account without a player profile of its own is set up without players."""
    home = load_fixture("home.html").replace(f'href="/player-profile/{OWN_ID}"', 'href="/"')
    aioclient_mock.post(URL_COOKIEWALL, text=home)
    aioclient_mock.get(URL_LOGIN_FORM, text=load_fixture("login.html"))
    aioclient_mock.post(URL_LOGIN, text=home)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == LOGIN


async def test_wrong_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A refused login shows an error."""
    mock_login(aioclient_mock, ok=False)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN, CONF_PASSWORD: "fout"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """An unreachable site shows an error."""
    aioclient_mock.post(URL_COOKIEWALL, exc=TimeoutError())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_own_profile_unreadable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If the own profile cannot be read, the account is still set up."""
    mock_login(aioclient_mock)
    aioclient_mock.get(URL_PROFILE, text="<html><a href='/user/LogOff'>x</a></html>")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].subentries == {}


async def test_account_already_configured(
    hass: HomeAssistant, mock_site: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The same login name cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOGIN: LOGIN.upper(), CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_site: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A new password is stored and the account reloaded."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["login"] == LOGIN
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "nieuw"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "nieuw"


async def test_reauth_wrong_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A wrong password during reauth shows an error."""
    mock_login(aioclient_mock, ok=False)
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "fout"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def _setup_account(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sam de Vries",
        unique_id=LOGIN,
        data={CONF_LOGIN: LOGIN, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _start_player(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PLAYER), context={"source": SOURCE_USER}
    )


async def test_add_own_profile(hass: HomeAssistant, mock_site: AiohttpClientMocker) -> None:
    """The menu offers the account's own profile while it is not followed."""
    entry = await _setup_account(hass)
    result = await _start_player(hass, entry)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["own", "search"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "own"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sam de Vries"
    assert result["unique_id"] == OWN_ID


async def test_add_player_by_search(hass: HomeAssistant, mock_site: AiohttpClientMocker) -> None:
    """Search by name, pick from the results (followed players left out)."""
    entry = await _setup_account(hass)
    result = await _start_player(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    assert result["step_id"] == "search"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "zzz"}
    )
    assert result["errors"] == {"query": "no_players_found"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "vries"}
    )
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema[CONF_PROFILE_ID].config["options"]
    assert [option["value"] for option in options] == [
        OTHER_ID,
        "00000000-0000-4000-8000-000000000003",
        OWN_ID,
    ]
    assert options[0]["label"] == "Robin de Vries (99990002) · T.V. Voorbeeld | VOORBEELDSTAD"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_PROFILE_ID: OTHER_ID}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Robin de Vries"
    assert result["data"][CONF_CLUB] == "T.V. Voorbeeld | VOORBEELDSTAD"


async def test_search_only_when_own_profile_followed(
    hass: HomeAssistant, mock_site: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """With the own profile followed, the flow goes straight to the search."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await _start_player(hass, config_entry)
    assert result["step_id"] == "search"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"query": "vries"}
    )
    options = result["data_schema"].schema[CONF_PROFILE_ID].config["options"]
    assert OWN_ID not in [option["value"] for option in options]


async def test_add_player_account_not_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Players can only be added to a working account."""
    config_entry.add_to_hass(hass)
    result = await _start_player(hass, config_entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_not_loaded"


def test_player_label() -> None:
    """Labels show the member number and club when known."""
    assert player_label(PlayerRef(OTHER_ID, "Robin de Vries", None, None)) == "Robin de Vries"
    assert (
        player_label(PlayerRef(OTHER_ID, "Robin de Vries", "99990002", "TC X"))
        == "Robin de Vries (99990002) · TC X"
    )
