"""Sensors per player: ratings, win-loss, results, next match, competition and ranking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import KnltbConfigEntry, KnltbPlayerCoordinator, PlayerData
from .entity import KnltbEntity
from .models import NL_TZ, Discipline, Match, Record, RecordType

PARALLEL_UPDATES = 0

RESULT_OPTIONS = ["won", "lost"]


def _result(won: bool | None) -> str | None:
    if won is None:
        return None
    return "won" if won else "lost"


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, kw_only=True)
class KnltbSensorDescription(SensorEntityDescription):
    """Describes a player sensor."""

    value_fn: Callable[[PlayerData, datetime], StateType | date | datetime]
    attrs_fn: Callable[[PlayerData, datetime], dict[str, Any] | None] = lambda _data, _now: None
    # Whether to create the sensor at all (e.g. padel only for padel players).
    exists_fn: Callable[[PlayerData], bool] = lambda _data: True


def _rating_value(discipline: Discipline) -> Callable[[PlayerData, datetime], float | None]:
    def value(data: PlayerData, _now: datetime) -> float | None:
        rating = data.profile.ratings.get(discipline)
        return rating.rating if rating else None

    return value


def _rating_attrs(discipline: Discipline) -> Callable[[PlayerData, datetime], dict[str, Any]]:
    def attrs(data: PlayerData, _now: datetime) -> dict[str, Any]:
        rating = data.profile.ratings.get(discipline)
        season = data.rating_page.ratings.get(discipline) if data.rating_page else None
        return {
            "playing_strength": rating.strength if rating else None,
            "next_playing_strength": rating.next_strength if rating else None,
            "next_from": _iso(rating.next_from) if rating else None,
            "trend": rating.trend.value if rating and rating.trend else None,
            "season": data.rating_page.season if data.rating_page else None,
            "start_rating": season.start_rating if season else None,
            "rated_matches": season.matches if season else None,
        }

    return attrs


def _total(data: PlayerData) -> Record | None:
    return data.profile.records.get(RecordType.TOTAL)


def _win_loss_attrs(wins: bool) -> Callable[[PlayerData, datetime], dict[str, Any]]:
    def attrs(data: PlayerData, _now: datetime) -> dict[str, Any]:
        result: dict[str, Any] = {}
        total = _total(data)
        if total and total.this_year:
            result["win_percentage"] = total.this_year.percentage
        if total and total.career:
            result["career"] = total.career.wins if wins else total.career.losses
            result["career_win_percentage"] = total.career.percentage
        for kind in (RecordType.SINGLES, RecordType.DOUBLES, RecordType.MIXED):
            record = data.profile.records.get(kind)
            if record and record.this_year:
                result[kind.value] = record.this_year.wins if wins else record.this_year.losses
        return result

    return attrs


def _match_attrs(match: Match | None) -> dict[str, Any] | None:
    if match is None:
        return None
    return {
        "kind": match.kind,
        "event": match.event,
        "draw": match.draw,
        "round": match.round,
        "result": _result(match.won),
        "score": match.score_text,
        "partners": list(match.partners),
        "opponents": list(match.opponents),
        "team": match.team,
        "opponent_team": match.opponent_team,
        "venue": match.venue,
        "duration": match.duration,
        "start": _iso(match.start),
    }


def _last_match(data: PlayerData, now: datetime) -> Match | None:
    return data.last_match(_today(now))


def _next_match(data: PlayerData, now: datetime) -> tuple[datetime, dict[str, Any]] | None:
    """Return the start and details of the next team or tournament match."""
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    if (found := data.next_team_match(now)) is not None:
        team, match = found
        assert match.start is not None
        candidates.append(
            (
                match.start,
                {
                    "kind": "competition",
                    "event": team.competition.name,
                    "round": match.round,
                    "team": team.team,
                    "home": match.home,
                    "away": match.away,
                    "category": team.category,
                    "level": team.level,
                },
            )
        )
    for match in data.matches:
        if match.kind == "tournament" and match.start and match.start >= now and match.won is None:
            candidates.append((match.start, {**(_match_attrs(match) or {})}))
            break
    return min(candidates, key=lambda item: item[0]) if candidates else None


def _competition_attrs(data: PlayerData, _now: datetime) -> dict[str, Any] | None:
    competition = data.current_competition
    if competition is None:
        return None
    first = competition.teams[0] if competition.teams else None
    page = next(
        (
            team.page
            for team in data.teams
            if team.competition is competition and first and team.team == first.team
        ),
        None,
    )
    return {
        "competition": competition.name,
        "start": _iso(competition.start),
        "end": _iso(competition.end),
        "team": first.team if first else None,
        "category": first.category if first else None,
        "level": first.level if first else None,
        "played": first.played if first else None,
        "points": page.points if page else None,
        "teams": [
            {
                "team": team.team,
                "category": team.category,
                "level": team.level,
                "position": team.position,
                "played": team.played,
            }
            for team in competition.teams
        ],
        "wins": competition.win_loss.wins if competition.win_loss else None,
        "losses": competition.win_loss.losses if competition.win_loss else None,
    }


def _competition_position(data: PlayerData, _now: datetime) -> int | None:
    competition = data.current_competition
    if competition is None or not competition.teams:
        return None
    return competition.teams[0].position


def _today(now: datetime) -> date:
    return now.astimezone(NL_TZ).date()


def _entries_attrs(data: PlayerData, now: datetime) -> dict[str, Any]:
    return {
        "entries": [
            {
                "tournament": entry.name,
                "location": entry.location,
                "start": _iso(entry.start),
                "end": _iso(entry.end),
                "kind": entry.kind,
                "events": [name for name, _path in entry.events],
                "status": entry.status,
                "priority": entry.priority,
            }
            for entry in data.relevant_entries(_today(now))
        ]
    }


def _ranking_attrs(data: PlayerData, _now: datetime) -> dict[str, Any] | None:
    if not data.profile.rankings:
        return None
    first, *others = data.profile.rankings
    return {
        "ranking": first.ranking,
        "category": first.category,
        "published": _iso(first.published),
        "best": first.best,
        "best_date": _iso(first.best_date),
        "points": first.points,
        "tournaments": first.tournaments,
        "other_rankings": [
            {"ranking": entry.ranking, "category": entry.category, "rank": entry.rank}
            for entry in others
        ],
    }


def _rating_sensor(discipline: Discipline, **kwargs: Any) -> KnltbSensorDescription:
    return KnltbSensorDescription(
        key=f"{discipline.value}_rating",
        translation_key=f"{discipline.value}_rating",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_rating_value(discipline),
        attrs_fn=_rating_attrs(discipline),
        **kwargs,
    )


SENSORS: tuple[KnltbSensorDescription, ...] = (
    _rating_sensor(Discipline.SINGLES),
    _rating_sensor(Discipline.DOUBLES),
    _rating_sensor(
        Discipline.PADEL, exists_fn=lambda data: Discipline.PADEL in data.profile.ratings
    ),
    KnltbSensorDescription(
        key="matches_won",
        translation_key="matches_won",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data, _now: (
            total.this_year.wins if (total := _total(data)) and total.this_year else None
        ),
        attrs_fn=_win_loss_attrs(wins=True),
    ),
    KnltbSensorDescription(
        key="matches_lost",
        translation_key="matches_lost",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data, _now: (
            total.this_year.losses if (total := _total(data)) and total.this_year else None
        ),
        attrs_fn=_win_loss_attrs(wins=False),
    ),
    KnltbSensorDescription(
        key="last_result",
        translation_key="last_result",
        device_class=SensorDeviceClass.ENUM,
        options=RESULT_OPTIONS,
        value_fn=lambda data, _now: (
            _result(data.profile.form[-1].won) if data.profile.form else None
        ),
        attrs_fn=lambda data, _now: {
            "last_five": [_result(result.won) for result in data.profile.form],
            "dates": [_iso(result.day) for result in data.profile.form],
        },
    ),
    KnltbSensorDescription(
        key="last_match",
        translation_key="last_match",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda data, now: match.day if (match := _last_match(data, now)) else None,
        attrs_fn=lambda data, now: _match_attrs(_last_match(data, now)),
    ),
    KnltbSensorDescription(
        key="next_match",
        translation_key="next_match",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, now: found[0] if (found := _next_match(data, now)) else None,
        attrs_fn=lambda data, now: found[1] if (found := _next_match(data, now)) else None,
    ),
    KnltbSensorDescription(
        key="tournament_entries",
        translation_key="tournament_entries",
        value_fn=lambda data, now: len(data.relevant_entries(_today(now))),
        attrs_fn=_entries_attrs,
        # Only the logged-in member's own entries are visible.
        exists_fn=lambda data: data.entries is not None,
    ),
    KnltbSensorDescription(
        key="competition_position",
        translation_key="competition_position",
        value_fn=_competition_position,
        attrs_fn=_competition_attrs,
    ),
    KnltbSensorDescription(
        key="ranking",
        translation_key="ranking",
        value_fn=lambda data, _now: (
            data.profile.rankings[0].rank if data.profile.rankings else None
        ),
        attrs_fn=_ranking_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnltbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of each player."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            (
                KnltbSensor(coordinator, description)
                for description in SENSORS
                if coordinator.data is None or description.exists_fn(coordinator.data)
            ),
            config_subentry_id=subentry_id,
        )


class KnltbSensor(KnltbEntity, SensorEntity):
    """A sensor of a player."""

    entity_description: KnltbSensorDescription

    def __init__(
        self, coordinator: KnltbPlayerCoordinator, description: KnltbSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | date | datetime:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the details."""
        return self.entity_description.attrs_fn(self.coordinator.data, dt_util.utcnow())
