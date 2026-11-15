"""Constants for the Voetbalcompetitie integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "voetbalcompetitie"
LOGGER = logging.getLogger(__package__)

# Config entry (one per voetbal.nl account)
CONF_REFRESH_TOKEN: Final = "refresh_token"  # noqa: S105 - a config key, not a secret

# Subentry (one per team)
SUBENTRY_TEAM: Final = "team"
CONF_TEAM_ID: Final = "team_id"
CONF_TEAM_NAME: Final = "team_name"
CONF_TEAM_CODE: Final = "team_code"
CONF_CLUB_ID: Final = "club_id"
CONF_CLUB_NAME: Final = "club_name"
CONF_SPORT: Final = "sport"

# Polling. The server allows clients to cache answers for 5 minutes to a day, so
# there is no point asking more often than every few minutes even during a match.
UPDATE_INTERVAL: Final = timedelta(minutes=30)
UPDATE_INTERVAL_MATCH: Final = timedelta(minutes=5)
UPDATE_INTERVAL_IDLE: Final = timedelta(hours=3)
UPDATE_INTERVAL_MIN: Final = timedelta(minutes=1)
# Pools without upcoming matches (an earlier phase of the season) rarely change.
FINISHED_POOL_REFRESH: Final = timedelta(hours=12)
# Venue details (address, pitch, dressing rooms) of matches in the coming week.
FACILITY_DAYS_AHEAD: Final = timedelta(days=7)
FACILITY_REFRESH: Final = timedelta(hours=3)
# The playing time of a pool (from one of its matches) is fetched once a day.
PLAYING_TIME_REFRESH: Final = timedelta(hours=24)

# Calendar slot: net playing time plus half-time; without it, a senior match.
HALF_TIME: Final = timedelta(minutes=15)
DEFAULT_MATCH_DURATION: Final = timedelta(minutes=105)

# Poll more often from this long before a match until this long after its start
# (played, result not entered yet).
MATCH_LEAD: Final = timedelta(minutes=30)
RESULT_WAIT: Final = timedelta(hours=3)

# Team settings (subentry data, changed with Reconfigure)
CONF_MEET_MINUTES_HOME: Final = "meet_minutes_home"
CONF_MEET_MINUTES_AWAY: Final = "meet_minutes_away"
CONF_WEATHER_ENTITY: Final = "weather_entity"
DEFAULT_MEET_MINUTES: Final = 30
# A club-entered departure time further from kick-off than this is not trusted.
MAX_MEET_LEAD: Final = timedelta(hours=4)

# Weather at the next match, from a weather entity of the user's choice
WEATHER_AHEAD: Final = timedelta(hours=48)
WEATHER_REFRESH: Final = timedelta(minutes=30)

# The matches as last seen (to notice changes), kept across restarts
STORAGE_VERSION: Final = 1

# Club subentry: every match of a club
SUBENTRY_CLUB: Final = "club"
CLUB_UPDATE_INTERVAL: Final = timedelta(hours=2)
CLUB_UPDATE_INTERVAL_MATCH_DAY: Final = timedelta(minutes=30)
CLUB_UPDATE_INTERVAL_RESULTS: Final = timedelta(minutes=15)
