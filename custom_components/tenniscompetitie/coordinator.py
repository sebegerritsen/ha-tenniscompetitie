"""Coordinator: one followed player's profile, rating, competitions and matches."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import KnltbAuthError, KnltbClient, KnltbConnectionError, KnltbError, player_path
from .changes import Change, detect, snapshot
from .const import (
    CONF_PROFILE_ID,
    DOMAIN,
    ISSUE_MARKUP_CHANGED,
    LOGGER,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_MATCH_DAY,
)
from .models import (
    NL_TZ,
    Competition,
    CompetitionTeam,
    Discipline,
    Entry,
    Match,
    Profile,
    RatingPage,
    RatingSeason,
    SeasonSummary,
    StandingRow,
    TeamMatch,
    TeamMatchDetail,
    TeamPage,
    Venue,
)
from .parsers import (
    KnltbParseError,
    find_tournament_player,
    name_key,
    parse_competition_matches,
    parse_competitions,
    parse_draw_page,
    parse_entries,
    parse_event_links,
    parse_profile,
    parse_rated_matches,
    parse_rating_page,
    parse_rating_summary,
    parse_team_match_page,
    parse_team_page,
    parse_tournament_matches,
    rating_series,
)

# Which pages failed to parse, per player, for the one "markup changed" repair.
DATA_PARSE_FAILURES: HassKey[dict[str, set[str]]] = HassKey(f"{DOMAIN}_parse_failures")

# A competition counts as current from this long before its start to after its end.
COMPETITION_MARGIN = timedelta(days=14)
# Look up the player's matches in an entered tournament from this long before it starts
# (draws and times are published a few days before).
TOURNAMENT_LOOKAHEAD = timedelta(days=14)
ENTRIES_PATH = "/user/entries"
# Raise when the stored rating history must be read again (it was read wrongly before).
HISTORY_FORMAT = 3
# How long fetched pages that change slowly are reused.
POOL_REFRESH = timedelta(hours=6)
TEAM_MATCH_REFRESH = timedelta(hours=12)
TEAM_MATCH_REFRESH_SOON = timedelta(hours=1)  # within two days of the match


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
    standing: tuple[StandingRow, ...] = ()
    # The next team match's own page: venue and (once set) the line-up.
    next_detail: TeamMatchDetail | None = None


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
    # The DSS rating after each rated match, per discipline (all seasons read so far),
    # and the seasons' summaries.
    rating_history: dict[Discipline, list[tuple[date, float]]] = field(default_factory=dict)
    seasons: list[SeasonSummary] = field(default_factory=list)
    # Venues of team matches, by home team (a team plays its home matches at its club).
    venues: dict[str, Venue] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
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

    def matches_on(self, day: date) -> list[tuple[datetime | None, Any]]:
        """Return the player's matches (team and own) on a day, by start time."""
        found: list[tuple[datetime | None, Any]] = [
            (match.start, (team, match)) for team, match in self.team_matches() if match.day == day
        ]
        found += [(match.start, match) for match in self.matches if match.day == day]
        return sorted(found, key=lambda item: item[0] or datetime.min.replace(tzinfo=NL_TZ))

    def venue_of(self, match: TeamMatch) -> Venue | None:
        """Return where a team match is played, if known."""
        return self.venues.get(name_key(match.home))

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
        # Slowly changing pages, with when they were fetched.
        self._pages: dict[str, tuple[datetime, str]] = {}
        self._venues: dict[str, Venue] = {}
        # Kept across restarts: the last update's state (for change alerts) and the
        # rating history read so far.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.{subentry.subentry_id}"
        )
        self._stored: dict[str, Any] = {}
        self._backfill: asyncio.Task[None] | None = None

    async def _async_setup(self) -> None:
        """Load what was kept from before Home Assistant stopped."""
        stored = await self._store.async_load()
        self._stored = stored if isinstance(stored, dict) else {}

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
        if data.rating_page is not None:
            # Also what the background reading of past seasons has stored meanwhile.
            self._fill_history(data, data.rating_page)
        self._note_changes(data, today)
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
        if data.rating_page is not None:
            await self._update_rating_history(data, data.rating_page, profile.name)
        data.venues = dict(self._venues)
        return data

    # --- Change alerts --------------------------------------------------------

    def _note_changes(self, data: PlayerData, today: date) -> None:
        """Compare with the previous update, and remember this one."""
        team_matches = [
            (team.team, team.competition.name, match) for team, match in data.team_matches()
        ]
        data.changes = detect(
            self._stored.get("snapshot"), data.matches, team_matches, data.profile.ratings, today
        )
        self._stored["snapshot"] = snapshot(data.matches, team_matches, data.profile.ratings, today)
        self._store.async_delay_save(lambda: self._stored, 1)

    # --- Pages that change slowly ---------------------------------------------

    async def _get_cached(self, path: str, max_age: timedelta) -> str:
        now = dt_util.utcnow()
        if (cached := self._pages.get(path)) is not None and now - cached[0] < max_age:
            return cached[1]
        html = await self.client.get(path)
        self._pages[path] = (now, html)
        return html

    # --- Rating history -------------------------------------------------------

    @staticmethod
    def _season_key(padel: bool, season: str) -> str:
        return f"{'padel' if padel else 'tennis'}|{season}"

    def _history(self) -> dict[str, Any]:
        history = self._stored.setdefault("history", {})
        if history.get("format") != HISTORY_FORMAT or not isinstance(history.get("seasons"), dict):
            # Read again whatever an older version of the integration stored.
            history.clear()
            history.update({"format": HISTORY_FORMAT, "seasons": {}})
        return history

    async def _update_rating_history(self, data: PlayerData, page: RatingPage, name: str) -> None:
        """Keep the current season's rated matches; read the other seasons in the background."""
        seasons = self._history()["seasons"]
        for season in page.seasons:
            if not season.active:
                continue
            stored = seasons.setdefault(self._season_key(season.padel, season.season), {})
            counts = stored.setdefault("counts", {})
            points = stored.setdefault("points", {})
            for discipline, current in page.ratings.items():
                if (discipline is Discipline.PADEL) != season.padel:
                    continue
                path = page.matches_paths.get(discipline)
                if path is None or counts.get(discipline.value) == current.matches:
                    continue
                try:
                    rated = parse_rated_matches(await self.client.get(path, page=False), name)
                except (KnltbError, KnltbParseError) as err:
                    LOGGER.debug("Could not read this season's rated matches: %s", err)
                    continue
                points[discipline.value] = [
                    [day.isoformat(), value]
                    for day, value in rating_series(rated, current.start_rating)
                ]
                counts[discipline.value] = current.matches
            self._store.async_delay_save(lambda: self._stored, 1)
        missing = [
            season
            for season in page.seasons
            if not season.active
            and season.summary_path
            and "summaries" not in seasons.get(self._season_key(season.padel, season.season), {})
        ]
        if missing and (self._backfill is None or self._backfill.done()):
            self._backfill = self.config_entry.async_create_background_task(
                self.hass, self._read_seasons(missing, page, name), f"{DOMAIN} rating history"
            )

    def _fill_history(self, data: PlayerData, page: RatingPage) -> None:
        """Put the stored history (and this season's figures) into the player's data."""
        history: dict[Discipline, list[tuple[date, float]]] = {}
        summaries: list[SeasonSummary] = []
        for key, stored in self._history()["seasons"].items():
            for discipline_value, points in (stored.get("points") or {}).items():
                try:
                    discipline = Discipline(discipline_value)
                except ValueError:
                    continue
                history.setdefault(discipline, []).extend(
                    (date.fromisoformat(day), float(value)) for day, value in points
                )
            for item in stored.get("summaries") or []:
                try:
                    summaries.append(
                        SeasonSummary(
                            season=key.split("|", 1)[1],
                            discipline=Discipline(item["discipline"]),
                            strength=item.get("strength"),
                            start_rating=item.get("start_rating"),
                            end_rating=item.get("end_rating"),
                            matches=item.get("matches"),
                        )
                    )
                except (KeyError, ValueError):
                    continue
        for discipline, current in page.ratings.items():
            summaries.append(
                SeasonSummary(
                    season=page.season or "",
                    discipline=discipline,
                    strength=current.strength,
                    start_rating=current.start_rating,
                    end_rating=current.rating,
                    matches=current.matches,
                )
            )
        data.rating_history = {d: sorted(set(points)) for d, points in history.items()}
        data.seasons = summaries

    async def _read_seasons(self, seasons: list[RatingSeason], page: RatingPage, name: str) -> None:
        """Read past seasons' summaries and rated matches (once; kept in storage)."""
        stored_seasons = self._history()["seasons"]
        for season in seasons:
            assert season.summary_path is not None
            try:
                summaries = parse_rating_summary(
                    await self.client.get(season.summary_path, page=False),
                    season.season,
                    padel=season.padel,
                )
                points: dict[str, list[list[Any]]] = {}
                for summary in summaries:
                    if summary.matches_path and summary.matches:
                        rated = parse_rated_matches(
                            await self.client.get(summary.matches_path, page=False), name
                        )
                        points[summary.discipline.value] = [
                            [day.isoformat(), value]
                            for day, value in rating_series(rated, summary.start_rating)
                        ]
            except (KnltbError, KnltbParseError) as err:
                LOGGER.debug("Could not read the rating history (%s); trying again later", err)
                break
            stored_seasons[self._season_key(season.padel, season.season)] = {
                "summaries": [
                    {
                        "discipline": s.discipline.value,
                        "strength": s.strength,
                        "start_rating": s.start_rating,
                        "end_rating": s.end_rating,
                        "matches": s.matches,
                    }
                    for s in summaries
                ],
                "points": points,
            }
            self._store.async_delay_save(lambda: self._stored, 1)
        if self.data is not None:
            self._fill_history(self.data, page)
            self.async_update_listeners()

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
        """Find the player's page in a tournament through the entered events' pages.

        Open tournaments link the events from the entry; for others (the Junior Tour)
        the tournament's list of events tells which page belongs to an event name.
        """
        if (path := self._tournament_pages.get(entry.tournament_id)) is not None:
            return path
        event_paths = [path for _event, path in entry.events if path is not None]
        if not event_paths and entry.events:
            links = parse_event_links(
                await self.client.get(f"/sport/events.aspx?id={entry.tournament_id}")
            )
            event_paths = [
                path for event, _path in entry.events if (path := links.get(name_key(event)))
            ]
        for event_path in event_paths:
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
        today = dt_util.now(NL_TZ).date()
        for team in competition.teams:
            if not team.team_path:
                continue
            try:
                page = parse_team_page(await self.client.get(team.team_path))
            except KnltbParseError as err:
                self._parse_failed(data, err)
                continue
            schedule = TeamSchedule(
                competition=competition,
                team=team.team,
                category=team.category,
                level=team.level,
                page=page,
            )
            if is_current(competition, today):
                schedule = await self._team_details(data, schedule, team, today)
            data.teams.append(schedule)
        if competition.player_path:
            try:
                data.competition_matches.extend(
                    parse_competition_matches(
                        await self.client.get(competition.player_path), competition.name, name
                    )
                )
            except KnltbParseError as err:
                self._parse_failed(data, err)

    async def _team_details(
        self, data: PlayerData, schedule: TeamSchedule, team: CompetitionTeam, today: date
    ) -> TeamSchedule:
        """Add a current team's pool standing and its next match's venue and line-up."""
        standing: tuple[StandingRow, ...] = ()
        if team.draw_path:
            played_lately = any(
                match.day is not None and today - timedelta(days=1) <= match.day <= today
                for match in schedule.page.matches
            )
            try:
                standing = tuple(
                    parse_draw_page(
                        await self._get_cached(
                            team.draw_path, timedelta(0) if played_lately else POOL_REFRESH
                        )
                    )
                )
            except KnltbParseError as err:
                self._parse_failed(data, err)
        detail = None
        upcoming = next(
            (
                match
                for match in schedule.page.matches
                if match.won is None and match.day is not None and match.day >= today and match.path
            ),
            None,
        )
        if upcoming is not None and upcoming.path:
            soon = upcoming.day is not None and upcoming.day - today <= timedelta(days=2)
            try:
                detail = parse_team_match_page(
                    await self._get_cached(
                        upcoming.path, TEAM_MATCH_REFRESH_SOON if soon else TEAM_MATCH_REFRESH
                    )
                )
            except KnltbParseError as err:
                self._parse_failed(data, err)
            if detail is not None and detail.venue is not None:
                self._venues[name_key(detail.home)] = detail.venue
        return replace(schedule, standing=standing, next_detail=detail)

    def _parse_failed(self, data: PlayerData, err: KnltbParseError) -> None:
        LOGGER.warning("Could not read a MijnKNLTB page (%s); the site may have changed", err)
        data.parse_failures.add(err.page)
