"""Tests for the page parsers, against anonymised saved pages."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from custom_components.tenniscompetitie.models import NL_TZ, Discipline, RecordType, Trend, WinLoss
from custom_components.tenniscompetitie.parsers import (
    KnltbParseError,
    is_logged_in,
    login_token,
    own_profile_id,
    parse_competition_matches,
    parse_competitions,
    parse_profile,
    parse_rating_page,
    parse_search,
    parse_team_page,
)

from .conftest import OWN_ID, load_fixture

NOT_A_PROFILE = "<html><body><h1>Er ging iets mis</h1><a href='/user/LogOff'>x</a></body></html>"


def test_profile_header() -> None:
    """Name, member number, club and the three ratings with next season's strength."""
    profile = parse_profile(load_fixture("profile.html"))
    assert profile.name == "Sam de Vries"
    assert profile.member_number == "99990001"
    assert profile.club == "T.V. Voorbeeld"

    singles = profile.ratings[Discipline.SINGLES]
    assert singles.strength == 6
    assert singles.rating == pytest.approx(6.1234)
    assert singles.next_strength == 5
    assert singles.next_from == date(2027, 1, 1)
    assert singles.trend is Trend.UP

    doubles = profile.ratings[Discipline.DOUBLES]
    assert (doubles.strength, doubles.next_strength, doubles.trend) == (7, 7, Trend.SAME)

    padel = profile.ratings[Discipline.PADEL]
    assert padel.strength == 8  # the padel icon before the number is not a digit
    assert padel.rating == pytest.approx(8.0)
    assert (padel.next_strength, padel.trend) == (9, Trend.DOWN)


def test_profile_statistics() -> None:
    """Win-loss per tab (career and this year) and the last five results."""
    profile = parse_profile(load_fixture("profile.html"))
    total = profile.records[RecordType.TOTAL]
    assert total.career == WinLoss(120, 80)
    assert total.this_year == WinLoss(30, 20)
    assert total.this_year.percentage == 60
    assert profile.records[RecordType.SINGLES].this_year == WinLoss(18, 10)
    assert profile.records[RecordType.DOUBLES].career == WinLoss(48, 38)
    assert profile.records[RecordType.MIXED].this_year == WinLoss(2, 1)

    assert [result.won for result in profile.form] == [True, False, True, True, False]
    assert profile.form[0].day == date(2026, 10, 3)
    assert profile.form[-1].day == date(2026, 10, 23)


def test_profile_rankings() -> None:
    """Every category of every ranking list, whatever the columns."""
    rankings = parse_profile(load_fixture("profile.html")).rankings
    assert len(rankings) == 3
    first = rankings[0]
    assert first.ranking == "KNLTB Junioren Ranglijst"
    assert first.category == "Jongens t/m 16 jaar"
    assert (first.rank, first.best, first.points, first.tournaments) == (150, 140, 300, 4)
    assert first.published == date(2026, 11, 10)
    assert first.best_date == date(2026, 10, 6)
    circuit = rankings[2]
    assert (circuit.ranking, circuit.category, circuit.rank) == (
        "Voorbeeld Circuit (jeugd)",
        "JE-17",
        12,
    )
    assert (circuit.best, circuit.points, circuit.tournaments) == (None, 8, 3)


def test_profile_tournaments() -> None:
    """Tournaments with the player's matches, seen from the player's side."""
    tournaments = parse_profile(load_fixture("profile.html")).tournaments
    assert [t.name for t in tournaments] == [
        "Voorbeeld Open Jeugdtoernooi 2026",
        "Winter Dubbeltoernooi 2026",
    ]
    first = tournaments[0]
    assert first.location == "TC Testpark | Voorbeeldstad"
    assert (first.start, first.end) == (date(2026, 10, 17), date(2026, 10, 24))

    semi, round_one = first.matches
    assert semi.kind == "tournament"
    assert (semi.event, semi.draw, semi.round) == (
        "Voorbeeld Open Jeugdtoernooi 2026",
        "Tennis JE 13-17",
        "Halve finale",
    )
    assert semi.won is False
    assert semi.opponents == ("Alex Jansen",)
    assert semi.partners == ()
    assert semi.score == ((2, 6), (6, 4), (10, 12))
    assert semi.score_text == "2-6 6-4 10-12"
    assert semi.day == date(2026, 10, 23)
    assert semi.start is None
    assert semi.venue == "Testpark - Baan 7"
    assert semi.duration == 86
    assert round_one.won is True
    assert round_one.draw == "JE 13-17 C"


def test_doubles_from_the_second_row() -> None:
    """The player's side is the row with the result tag; scores turn round with it."""
    doubles = parse_profile(load_fixture("profile.html")).tournaments[1]
    played, planned = doubles.matches
    assert played.won is True
    assert played.partners == ("Kim Smit",)
    assert played.opponents == ("Lee de Boer", "Noa Visser")
    assert played.score == ((6, 3), (6, 2))
    assert played.duration == 58

    # Not played yet: no result, the side is found by the player's name, and the time.
    assert planned.won is None
    assert planned.partners == ("Kim Smit",)
    assert planned.opponents == ("Jo Mulder", "Ries Bos")
    assert planned.score == ()
    assert planned.start == datetime(2026, 11, 14, 15, 0, tzinfo=NL_TZ)


def test_rating_page() -> None:
    """The selected season per discipline: strength, start rating, matches, rating."""
    page = parse_rating_page(load_fixture("rating.html"))
    assert page.season == "Seizoen 2026 - 2027"
    singles = page.ratings[Discipline.SINGLES]
    assert (singles.strength, singles.matches) == (6, 4)
    assert singles.start_rating == pytest.approx(6.2001)
    assert singles.rating == pytest.approx(6.1234)
    assert page.ratings[Discipline.DOUBLES].rating == pytest.approx(6.8765)
    padel = page.ratings[Discipline.PADEL]
    assert (padel.strength, padel.matches) == (8, 0)


def test_competitions() -> None:
    """Competitions newest first, with the player's teams and win-loss."""
    autumn, spring = parse_competitions(load_fixture("leagues.html"))
    assert autumn.name == "KNLTB najaar Tennis 2026"
    assert (autumn.start, autumn.end) == (date(2026, 9, 4), date(2026, 11, 29))
    assert autumn.win_loss == WinLoss(7, 3)
    assert autumn.player_path == "/league/00000000-0000-4000-8000-0000000000a1/player/6001"
    first, second = autumn.teams
    assert first.team == "VOORBEELD 1"
    assert first.category == "Jongens 13 t/m 17 jaar Zondag"
    assert first.level == "Hoofdklasse – Afdeling 4"
    assert first.team_path == "/league/00000000-0000-4000-8000-0000000000a1/team/1187"
    assert (first.position, first.played) == (5, 5)
    assert (second.team, second.position) == ("VOORBEELD 2", 3)
    assert spring.win_loss == WinLoss(13, 0)
    assert spring.teams[0].position == 1


def test_no_competitions() -> None:
    """A year without competitions is an empty list, not an error."""
    assert parse_competitions(load_fixture("leagues_empty.html")) == []


def test_team_page() -> None:
    """Standing, points and the schedule with start times."""
    page = parse_team_page(load_fixture("team.html"))
    assert (page.position, page.played, page.points) == (5, 5, 14)
    assert len(page.matches) == 4
    first = page.matches[0]
    assert first.round == "Ronde 1"
    assert first.start == datetime(2026, 9, 6, 11, 30, tzinfo=NL_TZ)
    assert (first.home, first.away, first.home_score, first.away_score) == (
        "VOORBEELD 1",
        "TESTCLUB 1",
        5,
        1,
    )
    assert first.won is True
    assert first.path == "/league/00000000-0000-4000-8000-0000000000a1/team-match/7019"
    assert page.matches[1].won is False
    upcoming = page.matches[2]
    assert (upcoming.home_score, upcoming.away_score, upcoming.won) == (None, None, None)


def test_team_page_without_schedule() -> None:
    """A team without a standing or schedule yet is not an error."""
    page = parse_team_page(load_fixture("team_empty.html"))
    assert (page.position, page.points, page.matches) == (None, None, ())


def test_competition_matches() -> None:
    """The player's own matches take the round and time of their team match."""
    matches = parse_competition_matches(
        load_fixture("league_player.html"), "KNLTB najaar Tennis 2026", "Sam de Vries"
    )
    assert len(matches) == 3
    singles, doubles, lost = matches
    assert singles.kind == "competition"
    assert (singles.event, singles.round, singles.draw) == (
        "KNLTB najaar Tennis 2026",
        "Ronde 1",
        "HE3",
    )
    assert singles.start == datetime(2026, 9, 6, 11, 30, tzinfo=NL_TZ)
    assert singles.day == date(2026, 9, 6)
    assert (singles.team, singles.opponent_team) == ("VOORBEELD 1", "TESTCLUB 1")
    assert singles.won is True
    # The player is second on the side: the partner is the other one.
    assert doubles.partners == ("Max Kok",)
    assert doubles.opponents == ("Rik Peters", "Tim Vos")
    assert doubles.score == ((6, 0), (4, 6), (13, 11))
    # The player's side is the second row: the score is turned round.
    assert lost.won is False
    assert lost.score == ((2, 6), (4, 6))
    assert lost.opponents == ("Dirk Meijer",)


def test_search() -> None:
    """Search results: profile id (lower case), name, member number and club."""
    players = parse_search(load_fixture("search.html"))
    assert [p.name for p in players] == ["Robin de Vries", "Jip Vriesema", "Sam de Vries"]
    assert players[0].member_number == "99990002"
    assert players[0].club == "T.V. Voorbeeld | VOORBEELDSTAD"
    assert players[2].profile_id == OWN_ID
    assert parse_search(load_fixture("search_empty.html")) == []


def test_session_helpers() -> None:
    """Logged in or not, the login form's token and the account's own profile."""
    assert is_logged_in(load_fixture("home.html"))
    assert not is_logged_in(load_fixture("login.html"))
    assert login_token(load_fixture("login.html")) == "test-token-0123456789"
    assert login_token(load_fixture("home.html")) is None
    assert own_profile_id(load_fixture("home.html")) == OWN_ID
    assert own_profile_id(load_fixture("login.html")) is None


@pytest.mark.parametrize(
    ("parser", "page"),
    [
        (parse_profile, "profile"),
        (parse_rating_page, "rating"),
        (parse_competitions, "competitions"),
        (parse_team_page, "team"),
    ],
)
def test_changed_markup(parser, page: str) -> None:
    """A page without the expected structure raises, naming the page."""
    with pytest.raises(KnltbParseError) as err:
        parser(NOT_A_PROFILE)
    assert err.value.page == page


def test_rating_page_without_ratings() -> None:
    """A rating page whose DSS blocks are gone raises."""
    html = load_fixture("profile.html")  # has the header but no DSS blocks
    with pytest.raises(KnltbParseError):
        parse_rating_page(html)
