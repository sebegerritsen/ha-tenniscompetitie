"""Parsers for MijnKNLTB pages (Tournament Software markup; see docs/api.md).

Each parser takes the HTML of one page and returns plain data from models.py. They
raise KnltbParseError when the page no longer has the structure they rely on (the site
changed), and leave out single items they cannot read rather than failing the page.
Labels are matched in Dutch and English, since an account can switch languages.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime
import re

from bs4 import BeautifulSoup, Tag

from .models import (
    NL_TZ,
    Competition,
    CompetitionTeam,
    Discipline,
    FormResult,
    Match,
    PlayerRef,
    Profile,
    RankingEntry,
    Rating,
    RatingPage,
    Record,
    RecordType,
    SeasonRating,
    TeamMatch,
    TeamPage,
    Tournament,
    Trend,
    WinLoss,
)

PROFILE_PATH = re.compile(r"^/player-profile/([0-9a-fA-F-]{36})/?$")
_DATE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_INT = re.compile(r"-?\d+")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_WIN_LOSS = re.compile(r"(\d+)\s*[-/]\s*(\d+)")
_DURATION = re.compile(r"^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$")
_SPACES = re.compile(r"\s+")

RECORD_TABS = {
    "tabStatsTotal": RecordType.TOTAL,
    "tabStatsSingles": RecordType.SINGLES,
    "tabStatsDoubles": RecordType.DOUBLES,
    "tabStatsMixed": RecordType.MIXED,
}
RANKING_COLUMNS = {
    "rank": "rank",
    "positie": "rank",
    "beste": "best",
    "best": "best",
    "punten": "points",
    "points": "points",
    "toernooien": "tournaments",
    "tournaments": "tournaments",
}


class KnltbParseError(Exception):
    """A page does not have the structure the parser expects: the site changed."""

    def __init__(self, page: str, detail: str) -> None:
        """Initialize with the page kind and what was missing."""
        super().__init__(f"{page}: {detail}")
        self.page = page


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None) -> str:
    """Return a node's text with whitespace collapsed ('' for no node)."""
    if node is None:
        return ""
    return _SPACES.sub(" ", node.get_text(" ", strip=True)).strip()


def _int(text: str | None) -> int | None:
    if text and (match := _INT.search(text)):
        return int(match.group())
    return None


def _float(text: str | None) -> float | None:
    if text and (match := _NUMBER.search(text)):
        return float(match.group().replace(",", "."))
    return None


def _date(text: str | None) -> date | None:
    """Parse the first `d-m-yyyy` in a text."""
    if text and (match := _DATE.search(text)):
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _time_element(node: Tag | None) -> datetime | None:
    """Parse a `<time datetime="yyyy-mm-dd hh:mm">` as Dutch local time."""
    if node is None or not (value := node.get("datetime")):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=NL_TZ) if parsed.tzinfo is None else parsed


def _has_class(node: Tag, name: str) -> bool:
    return name in (node.get("class") or [])


def _discipline(label: str) -> Discipline | None:
    label = label.casefold()
    if "padel" in label:
        return Discipline.PADEL
    if "enkel" in label or "single" in label:
        return Discipline.SINGLES
    if "dubbel" in label or "double" in label:
        return Discipline.DOUBLES
    return None


def _trend(node: Tag) -> Trend | None:
    for use in node.select("use"):
        href = str(use.get("xlink:href") or use.get("href") or "")
        if "caret-up" in href:
            return Trend.UP
        if "caret-down" in href:
            return Trend.DOWN
        if "circle" in href:
            return Trend.SAME
    return None


def _strength_tag(tag: Tag) -> tuple[Discipline | None, int | None, int | None, date | None]:
    """Read a playing strength tag: `title="Enkel: 5 vanaf 1-1-2027"` around ` 6 `.

    Returns the discipline, the strength shown, and the next strength and its date
    from the title.
    """
    title = str(tag.get("title") or "")
    label, _, rest = title.partition(":")
    next_strength = _int(_DATE.sub("", rest))
    shown = _int(_text(tag.select_one(".tag-duo__title")))
    return _discipline(label), shown, next_strength, _date(rest)


def is_logged_in(html: str) -> bool:
    """Return whether a page is shown to a logged-in member (it has a log-off link)."""
    return "/user/LogOff" in html


def login_token(html: str) -> str | None:
    """Return the anti-forgery token of the login form."""
    soup = _soup(html)
    field = soup.select_one('form input[name="__RequestVerificationToken"]')
    return str(field["value"]) if field and field.get("value") else None


def own_profile_id(html: str) -> str | None:
    """Return the logged-in member's own profile id (the 'Mijn profiel' link)."""
    soup = _soup(html)
    # The account menu's link comes first; fall back to any profile link in the menu.
    links = soup.select('a.dropdown-list__link[href^="/player-profile/"]') or soup.select(
        '.masthead a[href^="/player-profile/"]'
    )
    for link in links:
        if match := PROFILE_PATH.match(str(link["href"])):
            return match.group(1).lower()
    return None


def parse_search(html: str) -> list[PlayerRef]:
    """Parse the player search results (`/find/player?q=`)."""
    players: list[PlayerRef] = []
    seen: set[str] = set()
    for link in _soup(html).select('a.media__link[href^="/player-profile/"]'):
        if not (match := PROFILE_PATH.match(str(link["href"]))):
            continue
        profile_id = match.group(1).lower()
        if profile_id in seen:
            continue
        seen.add(profile_id)
        title = link.find_parent(class_="media__title")
        content = link.find_parent(class_="media__content")
        players.append(
            PlayerRef(
                profile_id=profile_id,
                name=_text(link),
                member_number=_member_number(title),
                club=_text(content.select_one(".media__subheading")) or None if content else None,
            )
        )
    return players


def _member_number(title: Tag | None) -> str | None:
    if title is None:
        return None
    aside = _text(title.select_one(".media__title-aside")).strip("()")
    return aside or None


def _header(soup: BeautifulSoup, page: str) -> Tag:
    header = soup.select_one(".page-head")
    if header is None or header.select_one(".media__title") is None:
        raise KnltbParseError(page, "no player header")
    return header


def parse_ratings(header: Tag) -> dict[Discipline, Rating]:
    """Parse the playing strength tags in a player's header."""
    ratings: dict[Discipline, Rating] = {}
    for tag in header.select("span.tag-duo[title]"):
        discipline, shown, next_strength, next_from = _strength_tag(tag)
        if discipline is None or discipline in ratings:
            continue
        ratings[discipline] = Rating(
            discipline=discipline,
            strength=shown,
            rating=_float(_text(tag.select_one(".tag-duo__value"))),
            next_strength=next_strength,
            next_from=next_from,
            trend=_trend(tag),
        )
    return ratings


def parse_profile(html: str) -> Profile:
    """Parse a player's profile page (`/player-profile/<id>`)."""
    soup = _soup(html)
    header = _header(soup, "profile")
    title = header.select_one(".media__title")
    name = _text(title.select_one(".nav-link__value")) if title else ""
    if not name:
        raise KnltbParseError("profile", "no player name")
    return Profile(
        name=name,
        member_number=_member_number(title),
        club=_text(header.select_one(".media__subheading .nav-link__value")) or None,
        ratings=parse_ratings(header),
        records=_records(soup),
        form=_form(soup.select_one("#tabStatsTotal")),
        rankings=tuple(_rankings(soup)),
        tournaments=tuple(_tournaments(soup, name)),
    )


def _records(soup: BeautifulSoup) -> dict[RecordType, Record]:
    records: dict[RecordType, Record] = {}
    for tab_id, kind in RECORD_TABS.items():
        if (tab := soup.find(id=tab_id)) is None:
            continue
        values: list[WinLoss] = []
        for value in tab.select(".list__value-start"):
            if match := _WIN_LOSS.search(_text(value)):
                values.append(WinLoss(int(match.group(1)), int(match.group(2))))
        if values:
            records[kind] = Record(
                career=values[0], this_year=values[1] if len(values) > 1 else None
            )
    return records


def _result_tag(tag: Tag) -> bool | None:
    if _has_class(tag, "tag--success"):
        return True
    if _has_class(tag, "tag--danger"):
        return False
    text = _text(tag).upper()
    if text == "W":
        return True
    if text in ("V", "L"):
        return False
    return None


def _form(tab: Tag | None) -> tuple[FormResult, ...]:
    if tab is None:
        return ()
    results = []
    for tag in tab.select(".match__status"):
        if (won := _result_tag(tag)) is not None:
            results.append(FormResult(won=won, day=_date(str(tag.get("title") or ""))))
    return tuple(results)


def _rankings(soup: BeautifulSoup) -> Iterable[RankingEntry]:
    for table in soup.select("table"):
        caption = table.find("caption")
        link = caption.select_one('a[href*="ranking"]') if caption else None
        if link is None:
            continue
        ranking = _text(link)
        published = _date(_text(caption.select_one(".tag")))
        columns = [
            RANKING_COLUMNS.get(_text(th).casefold() or str(th.get("title", "")).casefold())
            for th in table.select("thead th")
        ]
        for row in table.select("tbody tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            values: dict[str, Tag] = {
                column: cell for column, cell in zip(columns, cells, strict=False) if column
            }
            best = values.get("best")
            yield RankingEntry(
                ranking=ranking,
                category=_text(cells[0]),
                rank=_int(_text(values.get("rank"))),
                published=published,
                best=_int(best.find(string=True, recursive=False)) if best else None,
                best_date=_date(_text(best.select_one(".tag"))) if best else None,
                points=_int(_text(values.get("points"))),
                tournaments=_int(_text(values.get("tournaments"))),
            )


def _duration(text: str) -> int | None:
    match = _DURATION.match(text.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes = (int(part) if part else 0 for part in match.groups())
    return hours * 60 + minutes


def parse_match(
    node: Tag, *, kind: str, event: str | None = None, player: str | None = None
) -> Match | None:
    """Parse a `div.match` of individual players (not a team match).

    The followed player's side is the row with the W/V status tag; `player` (their
    name) tells them apart from a doubles partner. Returns None for a match without
    two sides.
    """
    rows = node.select(".match__row")
    if len(rows) != 2:
        return None

    def players(row: Tag) -> tuple[str, ...]:
        return tuple(
            name
            for value in row.select(".match__row-title-value-content")
            if (name := _text(value))
        )

    own = next((i for i, row in enumerate(rows) if row.select_one(".match__status")), None)
    won = _result_tag(rows[own].select_one(".match__status")) if own is not None else None
    if own is None and player:
        # No result yet (a planned match): find the player's side by name.
        own = next(
            (
                i
                for i, row in enumerate(rows)
                if player.casefold() in (name.casefold() for name in players(row))
            ),
            None,
        )
    if own is None:
        own = 0

    other = 1 - own
    own_players = players(rows[own])
    score: list[tuple[int, int]] = []
    for points in node.select(".match__result ul.points"):
        cells = [_int(_text(cell)) for cell in points.select(".points__cell")]
        if len(cells) == 2 and None not in cells:
            first, second = cells
            score.append((first, second) if own == 0 else (second, first))  # type: ignore[arg-type]
    note = None
    if result := node.select_one(".match__result"):
        leftover = _INT.sub("", _text(result)).strip(" -")
        note = leftover or None

    round_name = None
    duration = None
    for item in node.select(".match__header-title-item"):
        text = _text(item)
        if (minutes := _duration(text)) is not None:
            duration = minutes
        elif round_name is None and text:
            round_name = text

    day = None
    start = None
    venue = None
    for item in node.select(".match__footer-list-item"):
        text = _text(item)
        if (when := _time_element(item.find("time"))) is not None:
            start, day = when, when.date()
        elif (found := _date(text)) is not None:
            day = found
            if time := _TIME.search(_DATE.sub("", text)):
                start = datetime(
                    found.year, found.month, found.day, int(time[1]), int(time[2]), tzinfo=NL_TZ
                )
        elif (minutes := _duration(text)) is not None:
            duration = minutes
        elif text and venue is None:
            venue = text

    return Match(
        kind=kind,
        event=event,
        draw=None,
        round=round_name,
        day=day,
        start=start,
        venue=venue,
        duration=duration,
        won=won,
        partners=_partners(own_players, player),
        opponents=players(rows[other]),
        score=tuple(score),
        team=_text(rows[own].select_one(".match__row-title-header")) or None,
        opponent_team=_text(rows[other].select_one(".match__row-title-header")) or None,
        note=note,
    )


def _partners(players: tuple[str, ...], player: str | None) -> tuple[str, ...]:
    """Return the players on the followed player's side other than the player."""
    if player:
        others = tuple(name for name in players if name.casefold() != player.casefold())
        if len(others) < len(players):
            return others
    return players[1:]


def _tournaments(soup: BeautifulSoup, player: str | None = None) -> Iterable[Tournament]:
    """Parse the tournaments with the player's matches (profile and tournaments page)."""
    seen: list[Tag] = []
    for group in soup.select('ol.match-group[id^="TournamentMatchList_"]'):
        item = group.find_parent("li", class_="list__item")
        if item is not None and not any(item is other for other in seen):
            seen.append(item)
    for item in seen:
        link = item.select_one('.media__title a[href*="/sport/tournament"]')
        if link is None:
            continue
        name = str(link.get("title") or "") or _text(link)
        times = item.select(".media__subheading time")
        location = None
        for heading in item.select(".media__subheading"):
            if heading.find("time") is None and (text := _text(heading)):
                location = text
                break
        matches: list[Match] = []
        for group in item.select('ol.match-group[id^="TournamentMatchList_"]'):
            draw = group.find_previous_sibling("h5", class_="module-divider")
            for node in group.select("div.match"):
                if (
                    match := parse_match(node, kind="tournament", event=name, player=player)
                ) is not None:
                    matches.append(replace(match, draw=_text(draw) or None))
        yield Tournament(
            name=name,
            location=location,
            start=_time_element(times[0]).date() if times and _time_element(times[0]) else None,
            end=_time_element(times[-1]).date() if times and _time_element(times[-1]) else None,
            matches=tuple(matches),
        )


def parse_rating_page(html: str) -> RatingPage:
    """Parse the Rating page: the selected (current) season per discipline."""
    soup = _soup(html)
    _header(soup, "rating")
    ratings: dict[Discipline, SeasonRating] = {}
    season = None
    for card in soup.select(".module--card"):
        title = _text(card.select_one(".module__title-main"))
        if "DSS" not in title.upper():
            continue
        padel = "padel" in title.casefold()
        active = card.select_one(".page-nav__item--active")
        if season is None and active is not None:
            season = _text(active) or None
        container = card.select_one(".module-container[id]") or card
        for panel in container.select(".accordion"):
            header_title = panel.select_one(".rating-header__title")
            if header_title is None:
                continue
            discipline = Discipline.PADEL if padel else _discipline(_text(header_title))
            if discipline is None or discipline in ratings:
                continue
            strength_tag = header_title.select_one(".tag-duo")
            stats = _stats(panel.select_one(".rating-header__body") or panel)
            current = panel.select_one(".rating-header__stats .stats__value")
            ratings[discipline] = SeasonRating(
                discipline=discipline,
                strength=_int(_text(strength_tag.select_one(".tag-duo__title")))
                if strength_tag
                else None,
                start_rating=_float(stats.get("startrating")),
                matches=_int(stats.get("wedstrijden") or stats.get("matches")),
                rating=_float(current.find(string=True, recursive=False)) if current else None,
            )
    if not ratings:
        raise KnltbParseError("rating", "no DSS ratings")
    return RatingPage(season=season, ratings=ratings)


def _stats(node: Tag) -> dict[str, str]:
    """Return the `.stats` blocks under a node as {title (lowercase): value text}."""
    stats: dict[str, str] = {}
    for block in node.select(".stats__body"):
        title = block.select_one(".stats__title")
        value = block.select_one(".stats__value")
        if title is None or value is None:
            continue
        for key in (_text(title).casefold(), str(title.get("title") or "").casefold()):
            if key:
                stats.setdefault(key, _text(value))
    return stats


def parse_competitions(html: str) -> list[Competition]:
    """Parse the player's competitions page (`/player-profile/<id>/leagues`)."""
    soup = _soup(html)
    _header(soup, "competitions")
    competitions: list[Competition] = []
    for card in soup.select(".module--card"):
        link = card.select_one('.media__title a[href*="/sport/tournament"]')
        if link is None:
            continue
        times = card.select(".media__subheading time")
        teams: list[CompetitionTeam] = []
        for item in card.select("li.list__item"):
            team_link = item.select_one('.media__title a[href*="/team/"]')
            if team_link is None:
                continue
            division = item.select_one(".text--small a")
            category = _text(division.find("b")) if division else ""
            level = _text(division)
            if category and level.startswith(category):
                level = level[len(category) :]
            level = level.strip(" –-")
            stats = _stats(item)
            teams.append(
                CompetitionTeam(
                    team=_text(team_link),
                    category=category or None,
                    level=re.sub(r"\s*–\s*", " – ", level) or None,
                    team_path=str(team_link["href"]),
                    position=_int(stats.get("stand") or stats.get("standing")),
                    played=_int(stats.get("gespeeld") or stats.get("gs") or stats.get("played")),
                )
            )
        player_link = card.select_one('a[href*="/player/"]')
        win_loss = None
        for value in card.select(".stats--column .stats__value"):
            if match := _WIN_LOSS.search(_text(value)):
                win_loss = WinLoss(int(match.group(1)), int(match.group(2)))
        competitions.append(
            Competition(
                name=str(link.get("title") or "") or _text(link),
                start=_time_element(times[0]).date() if times and _time_element(times[0]) else None,
                end=_time_element(times[-1]).date() if times and _time_element(times[-1]) else None,
                teams=tuple(teams),
                player_path=str(player_link["href"]) if player_link else None,
                win_loss=win_loss,
            )
        )
    return competitions


def _team_match(node: Tag) -> TeamMatch | None:
    """Parse a `div.match--team-match`: two teams and the team score."""
    home = node.select_one(".team-match__name.is-team-1")
    away = node.select_one(".team-match__name.is-team-2")
    if home is None or away is None:
        return None
    header = node.select_one(".match__header-title")
    when = _time_element(header.find("time")) if header else None
    round_text = _text(header).split("•")[0].strip() if header else ""
    status = node.select_one(".match__status")
    wrapper = node.select_one("a.team-match__wrapper")
    return TeamMatch(
        round=round_text or None,
        start=when,
        day=when.date() if when else _date(_text(header)),
        home=_text(home),
        away=_text(away),
        home_score=_int(_text(node.select_one(".score .is-team-1"))),
        away_score=_int(_text(node.select_one(".score .is-team-2"))),
        won=_result_tag(status) if status else None,
        path=str(wrapper["href"]) if wrapper and wrapper.get("href") else None,
    )


def parse_team_page(html: str) -> TeamPage:
    """Parse a competition team's page (`/league/<id>/team/<n>`): standing and schedule."""
    soup = _soup(html)
    status = soup.select_one(".standing-status")
    matches = [
        match
        for node in soup.select("div.match--team-match")
        if (match := _team_match(node)) is not None
    ]
    if status is None and not matches and soup.select_one(".page-subhead .media__title") is None:
        # A team without a standing or schedule yet still has its name in the subhead.
        raise KnltbParseError("team", "no team page")
    stats = _stats(soup.select_one(".grid-template__spotlight") or soup)
    return TeamPage(
        position=_int(_text(status)),
        played=_int(stats.get("gespeeld") or stats.get("played")),
        points=_int(stats.get("punten") or stats.get("points")),
        matches=tuple(matches),
    )


def parse_competition_matches(
    html: str, competition: str | None = None, player: str | None = None
) -> list[Match]:
    """Parse a player's matches in one competition (`/league/<id>/player/<n>`).

    Each team match is followed by the player's own matches in it, which take the team
    match's round and time.
    """
    soup = _soup(html)
    matches: list[Match] = []
    for item in soup.select("li.match-group__item"):
        team_node = item.select_one("div.match--team-match")
        team_match = _team_match(team_node) if team_node else None
        for node in item.select("div.match"):
            if _has_class(node, "match--team-match"):
                continue
            match = parse_match(node, kind="competition", event=competition, player=player)
            if match is None:
                continue
            matches.append(
                replace(
                    match,
                    draw=match.round,
                    round=team_match.round if team_match else None,
                    day=team_match.day if team_match else match.day,
                    start=team_match.start if team_match else match.start,
                )
            )
    return matches
