"""What MijnKNLTB shows about a player, as plain data (see parsers.py and docs/api.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

# Times on the site are Dutch local time.
NL_TZ = ZoneInfo("Europe/Amsterdam")


class Discipline(StrEnum):
    """A rated discipline."""

    SINGLES = "singles"
    DOUBLES = "doubles"
    PADEL = "padel"  # padel is only played as doubles


class Trend(StrEnum):
    """Where the playing strength goes next season (the arrow next to it)."""

    UP = "up"
    DOWN = "down"
    SAME = "same"


class RecordType(StrEnum):
    """The tabs of the win-loss statistics."""

    TOTAL = "total"
    SINGLES = "singles"
    DOUBLES = "doubles"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class Rating:
    """A playing strength (1-9, 1 is best) and the KNLTB DSS rating behind it."""

    discipline: Discipline
    strength: int | None
    rating: float | None
    # The strength that applies from a date, shown once the KNLTB has calculated it
    # (around the turn of the year: next year's strength).
    next_strength: int | None = None
    next_from: date | None = None
    trend: Trend | None = None


@dataclass(frozen=True, slots=True)
class WinLoss:
    """Matches won and lost."""

    wins: int
    losses: int

    @property
    def played(self) -> int:
        """Return the number of matches played."""
        return self.wins + self.losses

    @property
    def percentage(self) -> int | None:
        """Return the share of matches won, in whole percent."""
        return round(100 * self.wins / self.played) if self.played else None


@dataclass(frozen=True, slots=True)
class Record:
    """Win-loss over a career and in the current year."""

    career: WinLoss | None
    this_year: WinLoss | None


@dataclass(frozen=True, slots=True)
class FormResult:
    """One of the latest results (won or lost) and its date."""

    won: bool
    day: date | None


@dataclass(frozen=True, slots=True)
class RankingEntry:
    """A player's position in one category of a KNLTB ranking list."""

    ranking: str
    category: str
    rank: int | None
    published: date | None = None
    best: int | None = None
    best_date: date | None = None
    points: int | None = None
    tournaments: int | None = None


@dataclass(frozen=True, slots=True)
class Match:
    """A singles or doubles match, seen from the followed player."""

    kind: str  # "tournament" or "competition"
    event: str | None  # tournament or competition
    draw: str | None  # tournament event ("onderdeel") or competition match type (HE3, HD1)
    round: str | None
    day: date | None
    start: datetime | None  # only when a time is known
    venue: str | None
    duration: int | None  # minutes
    won: bool | None
    partners: tuple[str, ...]
    opponents: tuple[str, ...]
    # Games per set, the player's side first.
    score: tuple[tuple[int, int], ...]
    team: str | None = None
    opponent_team: str | None = None
    note: str | None = None  # walkover, retired, ...

    @property
    def score_text(self) -> str | None:
        """Return the score as `6-2 4-6 10-12`."""
        if not self.score:
            return self.note
        return " ".join(f"{own}-{other}" for own, other in self.score)

    @property
    def sort_key(self) -> tuple[date, datetime]:
        """Sort by day, then by time (matches without one first)."""
        start = self.start or datetime.min.replace(tzinfo=NL_TZ)
        return (self.day or date.min, start)


@dataclass(frozen=True, slots=True)
class Tournament:
    """A tournament on the player's profile, with the player's matches in it."""

    name: str
    location: str | None
    start: date | None
    end: date | None
    matches: tuple[Match, ...]


@dataclass(frozen=True, slots=True)
class Profile:
    """The player's profile page: header, statistics, ranking and recent tournaments."""

    name: str
    member_number: str | None
    club: str | None
    ratings: dict[Discipline, Rating]
    records: dict[RecordType, Record] = field(default_factory=dict)
    form: tuple[FormResult, ...] = ()
    rankings: tuple[RankingEntry, ...] = ()
    tournaments: tuple[Tournament, ...] = ()


@dataclass(frozen=True, slots=True)
class SeasonRating:
    """A discipline's DSS figures in the current season (the Rating page)."""

    discipline: Discipline
    strength: int | None
    start_rating: float | None
    matches: int | None
    rating: float | None


@dataclass(frozen=True, slots=True)
class RatingPage:
    """The Rating page: the current season per discipline."""

    season: str | None
    ratings: dict[Discipline, SeasonRating]


@dataclass(frozen=True, slots=True)
class CompetitionTeam:
    """A team the player plays in, in one competition."""

    team: str
    category: str | None  # e.g. "Gemengd Zondag"
    level: str | None  # e.g. "1e klasse – Afdeling 3"
    team_path: str | None
    position: int | None
    played: int | None


@dataclass(frozen=True, slots=True)
class Competition:
    """A competition (e.g. KNLTB voorjaar) with the player's teams and win-loss."""

    name: str
    start: date | None
    end: date | None
    teams: tuple[CompetitionTeam, ...]
    player_path: str | None
    win_loss: WinLoss | None


@dataclass(frozen=True, slots=True)
class TeamMatch:
    """A match of a competition team against another team."""

    round: str | None
    start: datetime | None
    day: date | None
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    won: bool | None  # for the team, once played
    path: str | None


@dataclass(frozen=True, slots=True)
class TeamPage:
    """A competition team's page: standing and schedule."""

    position: int | None
    played: int | None
    points: int | None
    matches: tuple[TeamMatch, ...]


@dataclass(frozen=True, slots=True)
class PlayerRef:
    """A player found by search."""

    profile_id: str
    name: str
    member_number: str | None
    club: str | None
