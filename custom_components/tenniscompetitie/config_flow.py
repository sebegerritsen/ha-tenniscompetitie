"""Config flow: log in with a MijnKNLTB account, then add players as subentries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import create_client
from .api import KnltbAuthError, KnltbClient, KnltbError, player_path
from .const import (
    CONF_CLUB,
    CONF_LOGIN,
    CONF_NAME,
    CONF_PROFILE_ID,
    DOMAIN,
    LOGGER,
    SUBENTRY_PLAYER,
)
from .models import PlayerRef
from .parsers import KnltbParseError, parse_profile

CONF_QUERY = "query"
CONF_FOLLOW_OWN = "follow_own"
# Show a short list as radio buttons, a longer one as a dropdown.
LIST_MODE_MAX = 6
# A search can return many players; offer at most this many.
MAX_RESULTS = 100

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
LOGIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOGIN): TextSelector(TextSelectorConfig(autocomplete="username")),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)


def player_label(player: PlayerRef) -> str:
    """Return a player's label, such as `Sam de Vries (12345678) · TC Voorbeeld | STAD`."""
    label = player.name
    if player.member_number:
        label += f" ({player.member_number})"
    if player.club:
        label += f" · {player.club}"
    return label


def player_subentry(player: PlayerRef) -> ConfigSubentryData:
    """Return the subentry data of a followed player."""
    return ConfigSubentryData(
        data={
            CONF_PROFILE_ID: player.profile_id,
            CONF_NAME: player.name,
            CONF_CLUB: player.club,
        },
        subentry_type=SUBENTRY_PLAYER,
        title=player.name,
        unique_id=player.profile_id,
    )


async def fetch_player(client: KnltbClient, profile_id: str) -> PlayerRef:
    """Return a player's name and club from their profile page."""
    profile = parse_profile(await client.get(player_path(profile_id)))
    return PlayerRef(
        profile_id=profile_id,
        name=profile.name,
        member_number=profile.member_number,
        club=profile.club,
    )


async def _login(client: KnltbClient) -> dict[str, str]:
    """Log in; return form errors (empty on success)."""
    try:
        await client.login()
    except KnltbAuthError:
        return {"base": "invalid_auth"}
    except KnltbError as err:
        LOGGER.debug("Login failed: %s", err)
        return {"base": "cannot_connect"}
    return {}


class KnltbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in with a MijnKNLTB account; optionally follow the account's own profile."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._login_data: dict[str, str] = {}
        self._own: PlayerRef | None = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Players are added to an account as subentries."""
        return {SUBENTRY_PLAYER: PlayerSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the MijnKNLTB account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            login = user_input[CONF_LOGIN].strip()
            await self.async_set_unique_id(login.casefold())
            self._abort_if_unique_id_configured()
            client = create_client(self.hass, login, user_input[CONF_PASSWORD])
            try:
                errors = await _login(client)
                if not errors and client.own_profile_id:
                    try:
                        self._own = await fetch_player(client, client.own_profile_id)
                    except (KnltbError, KnltbParseError) as err:
                        LOGGER.debug("Could not read the account's own profile: %s", err)
            finally:
                client.close()
            if not errors:
                self._login_data = {CONF_LOGIN: login, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                if self._own is not None:
                    return await self.async_step_own_profile()
                return self._create_entry(follow_own=False)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA, {CONF_LOGIN: (user_input or {}).get(CONF_LOGIN)}
            ),
            errors=errors,
        )

    async def async_step_own_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether to follow the account's own player profile."""
        assert self._own is not None
        if user_input is not None:
            return self._create_entry(follow_own=user_input[CONF_FOLLOW_OWN])
        return self.async_show_form(
            step_id="own_profile",
            data_schema=vol.Schema(
                {vol.Required(CONF_FOLLOW_OWN, default=True): BooleanSelector()}
            ),
            description_placeholders={"name": self._own.name},
        )

    def _create_entry(self, *, follow_own: bool) -> ConfigFlowResult:
        subentries = [player_subentry(self._own)] if follow_own and self._own else []
        return self.async_create_entry(
            title=self._own.name if self._own else self._login_data[CONF_LOGIN],
            data=self._login_data,
            subentries=subentries,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The login stopped working: ask for the password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the (new) password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            client = create_client(self.hass, entry.data[CONF_LOGIN], user_input[CONF_PASSWORD])
            try:
                errors = await _login(client)
            finally:
                client.close()
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"login": entry.data[CONF_LOGIN]},
            errors=errors,
        )


class PlayerSubentryFlow(ConfigSubentryFlow):
    """Follow a player: the account's own profile, or anyone found by name."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._found: list[PlayerRef] = []

    @property
    def _client(self) -> KnltbClient:
        return self._get_entry().runtime_data.client

    def _configured(self) -> set[str]:
        return {
            subentry.unique_id
            for subentry in self._get_entry().subentries.values()
            if subentry.unique_id and subentry.subentry_type == SUBENTRY_PLAYER
        }

    def _add(self, player: PlayerRef) -> SubentryFlowResult:
        if player.profile_id in self._configured():
            return self.async_abort(reason="already_configured")
        subentry = player_subentry(player)
        return self.async_create_entry(
            title=subentry["title"], data=subentry["data"], unique_id=subentry["unique_id"]
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Offer the account's own profile (if not followed yet) or a search."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="account_not_loaded")
        try:
            own = await self._client.get_own_profile_id()
        except KnltbError as err:
            LOGGER.debug("Could not find the account's own profile: %s", err)
            own = None
        if own is None or own in self._configured():
            return await self.async_step_search()
        return self.async_show_menu(step_id="user", menu_options=["own", "search"])

    async def async_step_own(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Follow the account's own profile."""
        own = self._client.own_profile_id
        if own is None:
            return self.async_abort(reason="no_own_profile")
        try:
            player = await fetch_player(self._client, own)
        except (KnltbError, KnltbParseError) as err:
            LOGGER.debug("Could not read the account's own profile: %s", err)
            return self.async_abort(reason="cannot_connect")
        return self._add(player)

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for (part of) the player's name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            query = user_input[CONF_QUERY].strip()
            try:
                found = await self._client.search_players(query)
            except KnltbError as err:
                LOGGER.debug("Player search failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                configured = self._configured()
                self._found = [p for p in found if p.profile_id not in configured][:MAX_RESULTS]
                if not self._found:
                    errors[CONF_QUERY] = "no_players_found"
                else:
                    return await self.async_step_pick()
        return self.async_show_form(
            step_id="search",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_QUERY): TextSelector()}), user_input
            ),
            errors=errors,
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the player from the search results."""
        if user_input is not None:
            player = next(p for p in self._found if p.profile_id == user_input[CONF_PROFILE_ID])
            return self._add(player)
        options = [
            SelectOptionDict(value=player.profile_id, label=player_label(player))
            for player in self._found
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROFILE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST
                            if len(options) <= LIST_MODE_MAX
                            else SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
