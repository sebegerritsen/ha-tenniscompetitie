"""Diagnostics: what was read per player, without names, member numbers or profile ids.

Players followed here are often minors, so the diagnostics show the shape of the data
(which pages were read, how many matches, which ratings exist), not the data itself.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import KnltbConfigEntry, PlayerData


def _summary(data: PlayerData | None) -> dict[str, Any] | None:
    if data is None:
        return None
    profile = data.profile
    return {
        "ratings": sorted(discipline.value for discipline in profile.ratings),
        "ratings_with_value": sorted(
            discipline.value for discipline, rating in profile.ratings.items() if rating.rating
        ),
        "records": sorted(kind.value for kind in profile.records),
        "form_length": len(profile.form),
        "rankings": len(profile.rankings),
        "tournaments": len(profile.tournaments),
        "tournament_matches": sum(len(t.matches) for t in profile.tournaments),
        "rating_page": None
        if data.rating_page is None
        else {
            "season_found": data.rating_page.season is not None,
            "disciplines": sorted(d.value for d in data.rating_page.ratings),
        },
        "competitions": None if data.competitions is None else len(data.competitions),
        "competition_teams": [len(c.teams) for c in data.competitions or []],
        "team_pages": [
            {
                "position_found": team.page.position is not None,
                "points_found": team.page.points is not None,
                "matches": len(team.page.matches),
                "matches_with_time": sum(1 for m in team.page.matches if m.start),
            }
            for team in data.teams
        ],
        "competition_matches": len(data.competition_matches),
        "parse_failures": sorted(data.parse_failures),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KnltbConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for an account."""
    players = {
        f"player_{index}": {
            "update_interval": str(coordinator.update_interval),
            "last_update_success": coordinator.last_update_success,
            "data": _summary(coordinator.data),
        }
        for index, coordinator in enumerate(entry.runtime_data.coordinators.values(), start=1)
    }
    return {
        "players_followed": len(players),
        "own_profile_found": entry.runtime_data.client.own_profile_id is not None,
        "players": players,
    }
