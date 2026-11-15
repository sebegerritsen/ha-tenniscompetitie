"""Tests for the change detection behind the Match update event."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from custom_components.tenniscompetitie.changes import ChangeType, detect, snapshot
from custom_components.tenniscompetitie.models import NL_TZ, Discipline, Match, Rating, TeamMatch

TODAY = date(2026, 11, 14)

PLANNED = Match(
    kind="tournament",
    event="Najaarstoernooi",
    draw="GD6",
    round="Ronde 1",
    day=date(2026, 11, 14),
    start=datetime(2026, 11, 14, 20, 15, tzinfo=NL_TZ),
    venue="TV Teststad",
    duration=None,
    won=None,
    partners=("Kim Smit",),
    opponents=("Bram Dekker [2]", "Fien Dekker"),
    score=(),
)
TEAM = TeamMatch(
    round="Ronde 6",
    start=datetime(2026, 11, 15, 11, 30, tzinfo=NL_TZ),
    day=date(2026, 11, 15),
    home="VOORBEELD 1",
    away="OEFENBAAN 1",
    home_score=None,
    away_score=None,
    won=None,
    path="/league/x/team-match/7030",
)
SINGLES = Rating(Discipline.SINGLES, strength=6, rating=6.1234)


def _detect(previous, matches=(), teams=(), ratings=None):
    return detect(previous, list(matches), list(teams), ratings or {}, TODAY)


def _snap(matches=(), teams=(), ratings=None):
    return snapshot(list(matches), list(teams), ratings or {}, TODAY)


def test_first_update_only_records() -> None:
    """Without a previous update nothing is reported."""
    assert (
        _detect(None, [PLANNED], [("VOORBEELD 1", "C", TEAM)], {Discipline.SINGLES: SINGLES}) == []
    )


def test_new_and_moved_match_and_result() -> None:
    """A newly planned match, then the same match moved, then its result."""
    changes = _detect(_snap(), [PLANNED])
    assert [c.type for c in changes] == [ChangeType.NEW_MATCH]

    moved = replace(PLANNED, start=datetime(2026, 11, 14, 21, 0, tzinfo=NL_TZ))
    changes = _detect(_snap([PLANNED]), [moved])
    assert [c.type for c in changes] == [ChangeType.RESCHEDULED]
    assert changes[0].previous["start"] == "2026-11-14T20:15:00+01:00"

    played = replace(moved, won=True, score=((6, 3), (6, 4)))
    assert [c.type for c in _detect(_snap([moved]), [played])] == [ChangeType.RESULT]
    assert _detect(_snap([played]), [played]) == []


def test_result_of_a_match_never_seen() -> None:
    """A recent result for a match not seen before counts; an old one does not."""
    recent = replace(PLANNED, won=False, day=date(2026, 11, 13), start=None)
    old = replace(recent, round="Ronde 0", day=date(2026, 10, 1))
    assert [c.type for c in _detect(_snap(), [recent, old])] == [ChangeType.RESULT]


def test_seeding_does_not_make_a_new_match() -> None:
    """The same match with or without a seeding is one match."""
    unseeded = replace(PLANNED, opponents=("Bram Dekker", "Fien Dekker"))
    assert _detect(_snap([PLANNED]), [unseeded]) == []


def test_team_matches() -> None:
    """A team match coming up soon is new; a far one is not; a final score is a result."""
    far = replace(TEAM, day=date(2027, 4, 1), start=None, path="/league/x/team-match/8000")
    teams = [("VOORBEELD 1", "C", TEAM), ("VOORBEELD 1", "C", far)]
    changes = _detect(_snap(), teams=teams)
    assert [(c.type, c.team_match) for c in changes] == [(ChangeType.NEW_MATCH, TEAM)]

    final = replace(TEAM, home_score=4, away_score=2, won=True)
    changes = _detect(_snap(teams=teams), teams=[("VOORBEELD 1", "C", final)])
    assert [c.type for c in changes] == [ChangeType.TEAM_RESULT]
    assert changes[0].team == "VOORBEELD 1"

    moved = replace(TEAM, start=datetime(2026, 11, 15, 9, 0, tzinfo=NL_TZ))
    changes = _detect(_snap(teams=teams), teams=[("VOORBEELD 1", "C", moved)])
    assert [c.type for c in changes] == [ChangeType.RESCHEDULED]


def test_strength_and_rating() -> None:
    """Next season's strength appearing, and a changed rating."""
    before = _snap(ratings={Discipline.SINGLES: SINGLES})
    published = replace(SINGLES, next_strength=5, next_from=date(2027, 1, 1))
    assert [c.type for c in _detect(before, ratings={Discipline.SINGLES: published})] == [
        ChangeType.STRENGTH
    ]
    moved = replace(SINGLES, rating=6.0)
    changes = _detect(before, ratings={Discipline.SINGLES: moved})
    assert [c.type for c in changes] == [ChangeType.RATING]
    assert changes[0].previous["rating"] == 6.1234
    assert _detect(before, ratings={Discipline.SINGLES: SINGLES}) == []
