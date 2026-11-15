"""Detect what changed for a player between two updates (for the Match update event).

The state of the previous update is kept in Home Assistant's storage, so changes made
while Home Assistant was off are noticed too. The first update ever only records.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from .models import Discipline, Match, Rating, TeamMatch

# A result that appears for a match never seen before counts as new up to this old
# (tournament matches only show on the profile once played).
NEW_RESULT_AGE = timedelta(days=3)
# A team match that appears counts as new when it is this close (a whole season's
# schedule appears at once; that is not news match by match).
NEW_TEAM_MATCH_AHEAD = timedelta(days=14)
# Forget matches this long ago: nothing about them changes any more.
KEEP = timedelta(days=60)


class ChangeType(StrEnum):
    """What happened."""

    RESULT = "result"  # a match of the player has a result
    NEW_MATCH = "new_match"  # a match was planned (tournament) or comes up (team)
    RESCHEDULED = "rescheduled"  # a planned match moved to another time
    TEAM_RESULT = "team_result"  # a team match of the player's team has a final score
    STRENGTH = "strength"  # next season's playing strength was published or changed
    RATING = "rating"  # the DSS rating changed


@dataclass(frozen=True, slots=True)
class Change:
    """One change, with the match, team match or rating it is about."""

    type: ChangeType
    match: Match | None = None
    team_match: TeamMatch | None = None
    team: str | None = None
    competition: str | None = None
    rating: Rating | None = None
    previous: dict[str, Any] = field(default_factory=dict)


def key_of(match: Match) -> str:
    """Return a stable text key of an individual match."""
    opponents = ",".join(sorted(name.split(" [")[0].strip().casefold() for name in match.opponents))
    return "|".join(
        (match.kind, (match.event or "").casefold(), match.round or "", match.draw or "", opponents)
    )


def team_key_of(team: str, match: TeamMatch) -> str:
    """Return a stable text key of a team match."""
    return match.path or f"{team}|{match.home}|{match.away}|{match.day}"


def _match_state(match: Match) -> dict[str, Any]:
    return {
        "day": match.day.isoformat() if match.day else None,
        "start": match.start.isoformat() if match.start else None,
        "won": match.won,
    }


def _team_state(match: TeamMatch) -> dict[str, Any]:
    score = None
    if match.home_score is not None and match.away_score is not None:
        score = [match.home_score, match.away_score]
    return {
        "day": match.day.isoformat() if match.day else None,
        "start": match.start.isoformat() if match.start else None,
        "score": score,
    }


def _rating_state(rating: Rating) -> dict[str, Any]:
    return {
        "rating": rating.rating,
        "strength": rating.strength,
        "next_strength": rating.next_strength,
        "next_from": rating.next_from.isoformat() if rating.next_from else None,
    }


def snapshot(
    matches: Iterable[Match],
    team_matches: Iterable[tuple[str, str, TeamMatch]],
    ratings: dict[Discipline, Rating],
    today: date,
) -> dict[str, Any]:
    """Return what to remember of a player's update (JSON-able)."""
    oldest = today - KEEP
    return {
        "matches": {
            key_of(match): _match_state(match)
            for match in matches
            if match.day is None or match.day >= oldest
        },
        "team_matches": {
            team_key_of(team, match): _team_state(match)
            for team, _competition, match in team_matches
            if match.day is None or match.day >= oldest
        },
        "ratings": {discipline.value: _rating_state(r) for discipline, r in ratings.items()},
    }


def detect(
    previous: dict[str, Any] | None,
    matches: Iterable[Match],
    team_matches: Iterable[tuple[str, str, TeamMatch]],
    ratings: dict[Discipline, Rating],
    today: date,
) -> list[Change]:
    """Return what changed since the previous snapshot (nothing on the first update)."""
    if not isinstance(previous, dict):
        return []
    changes: list[Change] = []
    seen = previous.get("matches") or {}
    for match in matches:
        before = seen.get(key_of(match))
        if before is None:
            if match.won is not None:
                if match.day is not None and match.day >= today - NEW_RESULT_AGE:
                    changes.append(Change(ChangeType.RESULT, match=match))
            elif match.day is not None and match.day >= today:
                changes.append(Change(ChangeType.NEW_MATCH, match=match))
            continue
        if match.won is not None and before.get("won") is None:
            changes.append(Change(ChangeType.RESULT, match=match))
        elif (
            match.won is None
            and match.start is not None
            and before.get("start") not in (None, match.start.isoformat())
        ):
            changes.append(Change(ChangeType.RESCHEDULED, match=match, previous=before))

    seen_teams = previous.get("team_matches") or {}
    for team, competition, match in team_matches:
        before = seen_teams.get(team_key_of(team, match))
        state = _team_state(match)
        common = {"team_match": match, "team": team, "competition": competition}
        if before is None:
            if (
                state["score"] is None
                and match.day is not None
                and today <= match.day <= today + NEW_TEAM_MATCH_AHEAD
            ):
                changes.append(Change(ChangeType.NEW_MATCH, **common))
            continue
        if state["score"] is not None and before.get("score") is None:
            changes.append(Change(ChangeType.TEAM_RESULT, **common))
        elif (
            state["score"] is None
            and state["start"] is not None
            and before.get("start") not in (None, state["start"])
        ):
            changes.append(Change(ChangeType.RESCHEDULED, previous=before, **common))

    seen_ratings = previous.get("ratings") or {}
    for discipline, rating in ratings.items():
        before = seen_ratings.get(discipline.value)
        if not isinstance(before, dict):
            continue
        now = _rating_state(rating)
        if now["next_strength"] is not None and (
            now["next_strength"] != before.get("next_strength")
            or now["next_from"] != before.get("next_from")
        ):
            changes.append(Change(ChangeType.STRENGTH, rating=rating, previous=before))
        if (
            now["rating"] is not None
            and before.get("rating") is not None
            and abs(now["rating"] - before["rating"]) >= 0.00005
        ):
            changes.append(Change(ChangeType.RATING, rating=rating, previous=before))
    return changes
