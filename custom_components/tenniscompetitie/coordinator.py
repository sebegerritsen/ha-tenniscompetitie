"""Coordinator: one followed player's profile, rating, competitions and matches."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import KnltbAuthError, KnltbClient, KnltbConnectionError, KnltbError, player_path
from .const import (
    CONF_PROFILE_ID,
    DOMAIN,
    ISSUE_MARKUP_CHANGED,
    LOGGER,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_MATCH_DAY,
)
from .models import (
    NL_TZ,
    Competition,
    Entry,
    Match,
    Profile,
    RatingPage,
    TeamMatch,
    TeamPage,
)
from .parsers import (
    KnltbParseError,
    find_tournament_player,
    name_key,
    parse_competition_matches,
    parse_competitions,
    parse_entries,
    parse_profile,
    parse_rating_page,
    parse_team_page,
    parse_tournament_matches,
)

# Which pages failed to parse, per player, for the one "markup changed" repair.
DATA_PARSE_FAILURES: HassKey[dict[str, set[str]]] = HassKey(f"{DOMAIN}_parse_failures")

# A competition counts as current from this long before its start to after its end.
COMPETITION_MARGIN = timedelta(days=14)
# Look up the player's matches in an entered tournament from this long before it starts
# (draws and times are published a few days before).
TOURNAMENT_LOOKAHEAD = timedelta(days=14)
ENTRIES_PATH = "/user/entries"


@dataclass(slots=True)
class KnltbRuntimeData:
    """What a loaded account holds: its client and a coordinator per player."""

    client: KnltbClient
    coordinators: dict[str, KnltbPlayerCoordinator] = field(default_factory=dict)


type KnltbConfigEntry = ConfigEntry[KnltbRuntimeData]


@dataclass(frozen=True, slots=True)
class TeamSchedule:
    """A team of the player with its competition and page (standing, schedule)."""

    competition: Competition
    team: str
    category: str | None
    level: str | None
    page: TeamPage


@dataclass(slots=True)
class PlayerData:
    """Everything known about a player. A part is None when its page could not be read."""

    profile: Profile
    rating_page: RatingPage | None = None
    competitions: list[Competition] | None = None
    teams: list[TeamSchedule] = field(default_factory=list)
    competition_matches: list[Match] = field(default_factory=list)
    # The account's own tournament entries (None for other players: only the logged-in
    # member sees them) and the player's matches in entered tournaments, planned ones too.
    entries: list[Entry] | None = None
    tournament_matches: list[Match] = field(default_factory=list)
    parse_failures: set[str] = field(default_factory=set)

    @property
    def matches(self) -> list[Match]:
        """Return all known matches (tournaments and competitions), oldest first.

        A tournament match on both the profile and the tournament's own page counts once,
        as the tournament page has it (it is the more current of the two).
        """
        played = [match for t in self.profile.tournaments for match in t.matches]
        unique = {
            match_key(match): match
            for match in [*played, *self.competition_matches, *self.tournament_matches]
        }
        return sorted(unique.values(), key=lambda m: m.sort_key)

    def relevant_entries(self, today: date) -> list[Entry]:
        """Return the entries that still stand for tournaments that have not ended."""
        return sorted(
            (entry for entry in self.entries or [] if entry.is_relevant(today)),
            key=lambda entry: (entry.start or date.max, entry.priority or 0),
        )

    def last_match(self, today: date) -> Match | None:
        """Return the most recent match with a result."""
        played = [
            match
            for match in self.matches
            if match.day is not None and match.day <= today and match.won is not None
        ]
        return played[-1] if played else None

    @property
    def current_competition(self) -> Competition | None:
        """Return the newest competition (the site lists the newest first)."""
        return self.competitions[0] if self.competitions else None

    def team_matches(self) -> list[tuple[TeamSchedule, TeamMatch]]:
        """Return every match of the player's teams, oldest first."""
        matches = [(team, match) for team in self.teams for match in team.page.matches]
        return sorted(
            matches,
            key=lambda item: item[1].start or datetime.max.replace(tzinfo=NL_TZ),
        )

    def next_team_match(self, now: datetime) -> tuple[TeamSchedule, TeamMatch] | None:
        """Return the player's teams' next match that has not been played yet."""
        return next(
            (
                (team, match)
                for team, match in self.team_matches()
                if match.start is not None and match.start >= now and match.won is None
            ),
            None,
        )

    def has_match_on(self, day: date) -> bool:
        """Return whether a team or tournament match of the player is on a day."""
        if any(match.day == day for _team, match in self.team_matches()):
            return True
        if any(match.day == day for match in self.tournament_matches):
            return True
        spans = [(t.start, t.end) for t in self.profile.tournaments]
        spans += [(e.start, e.end) for e in self.entries or [] if e.accepted is not False]
        return any(
            start is not None and end is not None and start <= day <= end for start, end in spans
        )


def match_key(match: Match) -> tuple[str, str, str, str, frozenset[str]]:
    """Return what identifies a match across pages: event, round, draw, opponents."""
    opponents = frozenset(name_key(name) for name in match.opponents)
    return (
        match.kind,
        (match.event or "").casefold(),
        match.round or "",
        match.draw or "",
        opponents,
    )


def update_markup_issue(hass: HomeAssistant, key: str, pages: set[str]) -> None:
    """Show the repair "the site changed" while any player has a page that fails."""
    failures = hass.data.setdefault(DATA_PARSE_FAILURES, {})
    if pages:
        failures[key] = set(pages)
    else:
        failures.pop(key, None)
    failing = sorted({page for found in failures.values() for page in found})
    if failing:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MARKUP_CHANGED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MARKUP_CHANGED,
            translation_placeholders={"pages": ", ".join(failing)},
            learn_more_url="https://github.com/sebegerritsen/ha-tenniscompetitie/issues",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MARKUP_CHANGED)


def is_current(competition: Competition, today: date) -> bool:
    """Return whether a competition is about to start, running or just ended."""
    if competition.start is None or competition.end is None:
        return False
    return competition.start - COMPETITION_MARGIN <= today <= competition.end + COMPETITION_MARGIN


class KnltbPlayerCoordinator(DataUpdateCoordinator[PlayerData]):
    """Fetches a player's pages: profile, rating, competitions, teams and matches."""

    config_entry: KnltbConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: KnltbConfigEntry,
        subentry: ConfigSubentry,
        client: KnltbClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.subentry_id}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.subentry = subentry
        self.profile_id: str = subentry.data[CONF_PROFILE_ID]
        # The player's page in each entered tournament (found once, by name).
        self._tournament_pages: dict[str, str] = {}

    async def _async_update_data(self) -> PlayerData:
        try:
            data = await self._fetch()
        except KnltbAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except KnltbParseError as err:
            update_markup_issue(self.hass, self.subentry.subentry_id, {err.page})
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="markup_changed",
                translation_placeholders={"error": str(err)},
            ) from err
        except KnltbConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except KnltbError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": str(err)},
            ) from err
        update_markup_issue(self.hass, self.subentry.subentry_id, data.parse_failures)
        today = dt_util.now(NL_TZ).date()
        self.update_interval = (
            UPDATE_INTERVAL_MATCH_DAY
            if data.has_match_on(today) or data.has_match_on(today + timedelta(days=1))
            else UPDATE_INTERVAL
        )
        return data

    async def _fetch(self) -> PlayerData:
        """Fetch the pages. Only the profile is required; other parts may fail alone."""
        profile = parse_profile(await self.client.get(player_path(self.profile_id)))
        data = PlayerData(profile=profile)
        today = dt_util.now(NL_TZ).date()

        try:
            data.rating_page = parse_rating_page(
                await self.client.get(player_path(self.profile_id, "Rating"))
            )
        except KnltbParseError as err:
            self._parse_failed(data, err)

        try:
            competitions = parse_competitions(
                await self.client.get(player_path(self.profile_id, "leagues"))
            )
            if not competitions:
                # Early in the year: show last year's competitions until new ones start.
                competitions = parse_competitions(
                    await self.client.get(player_path(self.profile_id, f"leagues/{today.year - 1}"))
                )
            data.competitions = competitions
        except KnltbParseError as err:
            self._parse_failed(data, err)

        for competition in self._followed_competitions(data.competitions or [], today):
            await self._fetch_competition(data, competition, profile.name)

        if self.profile_id == await self.client.get_own_profile_id():
            await self._fetch_entries(data, profile.name, today)
        return data

    async def _fetch_entries(self, data: PlayerData, name: str, today: date) -> None:
        """Fetch the account's own tournament entries and its matches in them."""
        try:
            entries = parse_entries(await self.client.get(ENTRIES_PATH))
            if today.month == 12:
                # Entries for January tournaments are under next year.
                entries += parse_entries(
                    await self.client.get(
                        f"{ENTRIES_PATH}/GetUserEntryListByYear/{today.year + 1}", page=False
                    ),
                    partial=True,
                )
        except KnltbParseError as err:
            self._parse_failed(data, err)
            return
        data.entries = entries
        for entry in data.relevant_entries(today):
            if entry.start is not None and entry.start - TOURNAMENT_LOOKAHEAD > today:
                continue
            if (path := await self._tournament_page(entry, name)) is None:
                continue
            data.tournament_matches.extend(
                parse_tournament_matches(await self.client.get(path), entry.name, name)
            )

    async def _tournament_page(self, entry: Entry, name: str) -> str | None:
        """Find the player's page in a tournament through the entered events' pages."""
        if (path := self._tournament_pages.get(entry.tournament_id)) is not None:
            return path
        for _event, event_path in entry.events:
            if event_path is None:
                continue
            if (
                path := find_tournament_player(await self.client.get(event_path), name)
            ) is not None:
                self._tournament_pages[entry.tournament_id] = path
                return path
        return None

    @staticmethod
    def _followed_competitions(competitions: list[Competition], today: date) -> list[Competition]:
        """Return the newest competition and any other current one."""
        followed = [
            competition
            for index, competition in enumerate(competitions)
            if index == 0 or is_current(competition, today)
        ]
        return followed

    async def _fetch_competition(
        self, data: PlayerData, competition: Competition, name: str
    ) -> None:
        """Fetch a competition's team pages and the player's matches in it."""
        for team in competition.teams:
            if not team.team_path:
                continue
            try:
                page = parse_team_page(await self.client.get(team.team_path))
            except KnltbParseError as err:
                self._parse_failed(data, err)
                continue
            data.teams.append(
                TeamSchedule(
                    competition=competition,
                    team=team.team,
                    category=team.category,
                    level=team.level,
                    page=page,
                )
            )
        if competition.player_path:
            try:
                data.competition_matches.extend(
                    parse_competition_matches(
                        await self.client.get(competition.player_path), competition.name, name
                    )
                )
            except KnltbParseError as err:
                self._parse_failed(data, err)

    def _parse_failed(self, data: PlayerData, err: KnltbParseError) -> None:
        LOGGER.warning("Could not read a MijnKNLTB page (%s); the site may have changed", err)
        data.parse_failures.add(err.page)
