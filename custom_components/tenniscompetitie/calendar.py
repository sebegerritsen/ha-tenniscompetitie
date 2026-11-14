"""Calendar per player: the player's team matches and tournament matches."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import TEAM_MATCH_DURATION, TOURNAMENT_MATCH_DURATION
from .coordinator import KnltbConfigEntry, KnltbPlayerCoordinator, PlayerData, TeamSchedule
from .entity import KnltbEntity
from .models import Match, TeamMatch

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnltbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a calendar per player."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([KnltbCalendar(coordinator)], config_subentry_id=subentry_id)


def _join(*parts: str | None, sep: str = "\n") -> str | None:
    text = sep.join(part for part in parts if part)
    return text or None


def team_match_event(team: TeamSchedule, match: TeamMatch) -> CalendarEvent | None:
    """Return a team match as an event: timed when the start is known, else all day."""
    summary = f"{match.home} – {match.away}"
    if match.home_score is not None and match.away_score is not None:
        summary += f" ({match.home_score}–{match.away_score})"
    description = _join(
        team.competition.name, _join(team.category, team.level, sep=" – "), match.round
    )
    uid = match.path or f"{team.team}-{match.day}"
    if match.start is not None:
        return CalendarEvent(
            start=match.start,
            end=match.start + TEAM_MATCH_DURATION,
            summary=summary,
            description=description,
            uid=uid,
        )
    if match.day is not None:
        return CalendarEvent(
            start=match.day,
            end=match.day + timedelta(days=1),
            summary=summary,
            description=description,
            uid=uid,
        )
    return None


def tournament_match_event(match: Match) -> CalendarEvent | None:
    """Return a tournament match as an event: timed when the start is known, else all day."""
    if match.day is None:
        return None
    opponents = " / ".join(match.opponents)
    summary = _join(match.event, _join(match.round, opponents, sep=": "), sep=" – ") or "Match"
    if match.score_text:
        summary += f" ({match.score_text})"
    description = _join(
        match.draw,
        f"Partner: {' / '.join(match.partners)}" if match.partners else None,
        match.venue,
    )
    key = f"{match.event}|{match.draw}|{match.round}|{match.day}|{opponents}"
    uid = hashlib.sha1(key.encode()).hexdigest()  # noqa: S324 - an id, not security
    if match.start is not None:
        return CalendarEvent(
            start=match.start,
            end=match.start + TOURNAMENT_MATCH_DURATION,
            summary=summary,
            description=description,
            location=match.venue,
            uid=uid,
        )
    return CalendarEvent(
        start=match.day,
        end=match.day + timedelta(days=1),
        summary=summary,
        description=description,
        location=match.venue,
        uid=uid,
    )


def player_events(data: PlayerData) -> list[CalendarEvent]:
    """Return all events of a player, in start order."""
    events = [
        event
        for team, match in data.team_matches()
        if (event := team_match_event(team, match)) is not None
    ]
    events += [
        event
        for match in data.matches
        if match.kind == "tournament" and (event := tournament_match_event(match)) is not None
    ]
    return sorted(events, key=lambda event: event.start_datetime_local)


def _local(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return dt_util.as_local(value)
    return dt_util.start_of_local_day(value)


class KnltbCalendar(KnltbEntity, CalendarEntity):
    """The matches of a player's teams and the player's tournament matches."""

    _attr_translation_key = "matches"

    def __init__(self, coordinator: KnltbPlayerCoordinator) -> None:
        """Initialize the calendar."""
        super().__init__(coordinator, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        """Return the event going on now, or else the next one."""
        now = dt_util.now()
        return next(
            (event for event in player_events(self.coordinator.data) if _local(event.end) > now),
            None,
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the events between two moments."""
        return [
            event
            for event in player_events(self.coordinator.data)
            if _local(event.start) < end_date and _local(event.end) > start_date
        ]
