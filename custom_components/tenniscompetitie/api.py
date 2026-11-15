"""Async client for MijnKNLTB (mijnknltb.toernooi.nl); see docs/api.md.

MijnKNLTB has no API: the client logs in the way a browser does and fetches pages,
which parsers.py reads. It accepts only the necessary cookies on the cookie wall,
logs in with the form's anti-forgery token, and logs in again when a request lands on
the login page (the login cookie lasts 7 days). One client (and cookie jar) per
account; requests are made one at a time.
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
import logging
import time
from urllib.parse import quote

import aiohttp
from yarl import URL

from .models import PlayerRef
from .parsers import is_logged_in, login_token, own_profile_id, parse_search

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://mijnknltb.toernooi.nl"
USER_AGENT = "ha-tenniscompetitie (+https://github.com/sebegerritsen/ha-tenniscompetitie)"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# The cookie wall's purposes: 1 = necessary, 2 = preferences (no statistics/marketing).
COOKIE_PURPOSES = ("1", "2")
LOGIN_PATHS = ("/user/login", "/cookiewall")


class KnltbError(Exception):
    """Base class for errors talking to MijnKNLTB."""


class KnltbConnectionError(KnltbError):
    """MijnKNLTB could not be reached, timed out or had a server error."""


class KnltbAuthError(KnltbError):
    """The login name or password was refused."""


class KnltbResponseError(KnltbError):
    """MijnKNLTB answered something unexpected."""


def player_path(profile_id: str, page: str = "") -> str:
    """Return the path of a player's profile page, e.g. `…/Rating`."""
    return f"/player-profile/{profile_id}{'/' + page if page else ''}"


class KnltbClient:
    """A logged-in MijnKNLTB session for one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        login: str,
        password: str,
        *,
        spacing: float = 0.0,
    ) -> None:
        """Initialize with a session that has its own cookie jar."""
        self._session = session
        self._login = login
        self._password = password
        self._spacing = spacing
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self.own_profile_id: str | None = None

    async def _request(
        self,
        method: str,
        path: str,
        data: list[tuple[str, str]] | None = None,
        *,
        xhr: bool = False,
    ) -> tuple[URL, str]:
        """Make one request; return the final URL (after redirects) and the body."""
        if self._spacing and (wait := self._last_request + self._spacing - time.monotonic()) > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()
        try:
            async with self._session.request(
                method,
                BASE_URL + path,
                data=data,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "nl-NL,nl;q=0.9",
                    # Parts of pages are only served to the page's own scripts.
                    **({"X-Requested-With": "XMLHttpRequest"} if xhr else {}),
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise KnltbConnectionError(f"{method} {path}: HTTP {response.status}")
                if response.status >= HTTPStatus.BAD_REQUEST:
                    raise KnltbResponseError(f"{method} {path}: HTTP {response.status}")
                return response.url, await response.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise KnltbConnectionError(f"{method} {path}: {err!r}") from err

    async def _log_in(self) -> None:
        """Accept the necessary cookies and log in."""
        await self._request(
            "POST",
            "/cookiewall/Save",
            [
                ("ReturnUrl", "/"),
                ("SettingsOpen", "false"),
                *(("CookiePurposes", purpose) for purpose in COOKIE_PURPOSES),
            ],
        )
        _url, body = await self._request("GET", "/user/login?ReturnUrl=%2f")
        if not is_logged_in(body):
            if (token := login_token(body)) is None:
                raise KnltbResponseError("The login form was not found")
            _url, body = await self._request(
                "POST",
                "/user",
                [
                    ("Login", self._login),
                    ("Password", self._password),
                    ("ReturnUrl", "/"),
                    ("__RequestVerificationToken", token),
                ],
            )
            if not is_logged_in(body):
                if login_token(body) is not None:
                    raise KnltbAuthError("Login refused")
                raise KnltbResponseError("Not logged in after posting the login form")
        if self.own_profile_id is None:
            self.own_profile_id = own_profile_id(body)
        _LOGGER.debug("Logged in to MijnKNLTB")

    async def login(self) -> None:
        """Log in (also checks the credentials)."""
        async with self._lock:
            await self._log_in()

    async def get(self, path: str, *, page: bool = True) -> str:
        """Fetch a page, logging in (again) when the site asks for it.

        `page=False` is for a part of a page loaded on its own (as the page's scripts do):
        it has no account menu, so only a redirect to the login page tells that the
        session ended.
        """

        def logged_out(url: URL, body: str) -> bool:
            return url.path.startswith(LOGIN_PATHS) or (page and not is_logged_in(body))

        async with self._lock:
            url, body = await self._request("GET", path, xhr=not page)
            if logged_out(url, body):
                await self._log_in()
                url, body = await self._request("GET", path, xhr=not page)
                if logged_out(url, body):
                    raise KnltbAuthError(f"Still not logged in for {path}")
            return body

    def close(self) -> None:
        """Let go of the session and its cookies.

        The session comes from Home Assistant's helper, which shares one connection pool
        between sessions: detach it rather than closing it.
        """
        self._session.detach()

    async def get_own_profile_id(self) -> str | None:
        """Return the account's own player profile id, if it has one."""
        if self.own_profile_id is None:
            self.own_profile_id = own_profile_id(await self.get("/"))
        return self.own_profile_id

    async def search_players(self, query: str) -> list[PlayerRef]:
        """Search players by name."""
        return parse_search(await self.get(f"/find/player?q={quote(query)}"))
