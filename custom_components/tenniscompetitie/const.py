"""Constants for the Tenniscompetitie integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "tenniscompetitie"
LOGGER = logging.getLogger(__package__)

# Config entry (one per MijnKNLTB account)
CONF_LOGIN: Final = "login"

# Subentries (one per followed player)
SUBENTRY_PLAYER: Final = "player"
CONF_PROFILE_ID: Final = "profile_id"
CONF_NAME: Final = "name"
CONF_CLUB: Final = "club"

# Ratings change at most weekly and results a few times a day, so poll gently: the
# player's pages every few hours, more often on and around days with a match.
UPDATE_INTERVAL: Final = timedelta(hours=3)
UPDATE_INTERVAL_MATCH_DAY: Final = timedelta(minutes=30)
# Space the requests of one account out a little.
REQUEST_SPACING: Final = 1.0

# Calendar slot lengths: the site gives start times only (and often only a date).
TEAM_MATCH_DURATION: Final = timedelta(hours=5)
TOURNAMENT_MATCH_DURATION: Final = timedelta(hours=2)

ATTRIBUTION: Final = "Data: KNLTB, MijnKNLTB (Tournament Software)"
ISSUE_MARKUP_CHANGED: Final = "markup_changed"

# The previous update (for change alerts) and the rating history, per player, in
# .storage/tenniscompetitie.<entry id>.<subentry id>
STORAGE_VERSION: Final = 1
