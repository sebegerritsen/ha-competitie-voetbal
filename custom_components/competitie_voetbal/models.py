"""Data models for the voetbal.nl app API, parsed leniently from its JSON.

The API is not officially documented (see docs/api.md), so every field is optional here:
a missing or renamed field gives None instead of an exception. Only competition data is
parsed; person data is never requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

NL_TZ = ZoneInfo("Europe/Amsterdam")
# Public club logos, as on voetbal.nl.
LOGO_URL = "https://logoapi.voetbal.nl/logo.php?clubcode={club_id}"


class MatchStatus(StrEnum):
    """Status of a match."""

    SCHEDULED = "scheduled"
    FINAL = "final"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"


_STATUSES = {"SCHEDULED": MatchStatus.SCHEDULED, "FINAL": MatchStatus.FINAL}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_datetime(value: Any) -> datetime | None:
    """Parse the API's timestamps, such as `2026-11-14T10:00:00+0100`."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NL_TZ)
    return parsed


def _status(data: dict[str, Any]) -> tuple[MatchStatus, str | None]:
    """Return a match's status and the reason it was not played normally, if any.

    A called-off match keeps its status (SCHEDULED) and says why in DisciplineStatus,
    e.g. `ADO` / "Afgelast door vereniging".
    """
    status = _STATUSES.get(str(data.get("Status") or "").upper(), MatchStatus.UNKNOWN)
    code = (_str(data.get("DisciplineStatus")) or "").upper()
    note = _str(data.get("DisciplineStatusDescription"))
    text = (note or "").casefold()
    if "afgelast" in text or (code.startswith("A") and len(code) == 3 and not note):
        return MatchStatus.CANCELLED, note or code
    if "gestaakt" in text:
        return MatchStatus.ABANDONED, note
    return status, note if code else None


@dataclass(frozen=True, slots=True)
class Club:
    """A club, from club/Clubs."""

    id: str
    name: str
    city: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Club | None:
        """Parse a club; None without an id."""
        club_id = _str(data.get("ClubId"))
        if club_id is None:
            return None
        return cls(id=club_id, name=_str(data.get("ClubName")) or club_id, city=_city(data))

    def matches_query(self, query: str) -> bool:
        """Return whether a search text occurs in the club's name or town."""
        needle = query.casefold().strip()
        return bool(needle) and any(
            needle in value.casefold() for value in (self.name, self.city or "")
        )


def _city(data: dict[str, Any]) -> str | None:
    """Return a town name as written normally (the API writes `DEN HAAG`)."""
    city = _str(data.get("City"))
    if city is None or not city.isupper():
        return city
    return " ".join(part[:1] + part[1:].lower() for part in city.split(" "))


@dataclass(frozen=True, slots=True)
class TeamRef:
    """A team as it appears in lists, programmes, results and standings."""

    id: str
    name: str
    code: str | None = None
    club_id: str | None = None
    club_name: str | None = None
    sport: str | None = None
    classes: tuple[str, ...] = ()
    # The club's home kit: shirt, shorts, socks (as the club describes them).
    colours: tuple[str | None, str | None, str | None] | None = None

    @property
    def logo(self) -> str | None:
        """Return the URL of the club's logo."""
        return LOGO_URL.format(club_id=self.club_id) if self.club_id else None

    @classmethod
    def from_api(cls, data: Any) -> TeamRef | None:
        """Parse a team; None without an id."""
        data = _dict(data)
        team_id = _str(data.get("PublicTeamId"))
        if team_id is None:
            return None
        club = _dict(data.get("Club"))
        code = _str(data.get("TeamCode"))
        outfit = _dict(_dict(club.get("ClubColors")).get("HomeOutfit"))
        kit = (_str(outfit.get("Shirt")), _str(outfit.get("Shorts")), _str(outfit.get("Stocking")))
        return cls(
            id=team_id,
            name=_str(data.get("TeamName")) or team_id,
            code=None if code == "-" else code,
            club_id=_str(club.get("ClubId")),
            club_name=_str(club.get("ClubName")),
            sport=_str(data.get("SportDescription")),
            classes=tuple(
                name
                for item in _list(data.get("Class"))
                if (name := _str(_dict(item).get("ClassName")))
            ),
            colours=kit if any(kit) else None,
        )


def _teams(items: Any) -> list[TeamRef]:
    return [team for item in _list(items) if (team := TeamRef.from_api(item)) is not None]


def parse_favorite_teams(answer: Any) -> list[TeamRef]:
    """Parse user/favorite/Favorites: the teams the account follows."""
    return _teams(_dict(answer).get("FavoriteTeam"))


def parse_clubs(answer: Any) -> list[Club]:
    """Parse club/Clubs."""
    return [
        club
        for item in _list(_dict(answer).get("Club"))
        if (club := Club.from_api(_dict(item))) is not None
    ]


def parse_club_teams(answer: Any) -> list[TeamRef]:
    """Parse club/ClubTeams."""
    return _teams(_dict(answer).get("ClubTeam"))


@dataclass(frozen=True, slots=True)
class Pool:
    """A pool (poule) the team plays in this season."""

    id: int
    name: str | None
    class_name: str | None
    kind: str | None
    next_match: datetime | None
    previous_match: datetime | None

    @classmethod
    def from_api(cls, data: Any) -> Pool | None:
        """Parse a pool; None without an id."""
        data = _dict(data)
        pool_id = _int(data.get("PoolId"))
        if pool_id is None:
            return None
        return cls(
            id=pool_id,
            name=_str(data.get("PoolName")),
            class_name=_str(data.get("ClassName")),
            kind=_str(data.get("CompetitionKind")),
            next_match=parse_datetime(data.get("NextMatchDate")),
            previous_match=parse_datetime(data.get("PreviousMatchDate")),
        )

    @property
    def is_cup(self) -> bool:
        """Return whether this is a cup (beker) competition."""
        return self.kind == "TROPHY_COMPETITION"

    @property
    def label(self) -> str | None:
        """Return the pool as people write it: `3e klasse (2e fase) – 24`."""
        parts = [part for part in (self.class_name, self.name) if part]
        return " – ".join(parts) or None


@dataclass(frozen=True, slots=True)
class Match:
    """A match, from a programme (upcoming) or from results (played)."""

    id: str
    start: datetime | None
    status: MatchStatus
    home: TeamRef
    away: TeamRef
    status_note: str | None = None
    venue: str | None = None
    pool: Pool | None = None
    number: int | None = None
    score_home: int | None = None
    score_away: int | None = None
    extra_time_home: int | None = None
    extra_time_away: int | None = None
    penalties_home: int | None = None
    penalties_away: int | None = None

    @classmethod
    def from_api(cls, data: Any) -> Match | None:
        """Parse a match (a programme item's `Match`, or a `MatchResult`)."""
        data = _dict(data)
        match_id = _str(data.get("PublicMatchId"))
        home, away = TeamRef.from_api(data.get("HomeTeam")), TeamRef.from_api(data.get("AwayTeam"))
        if match_id is None or home is None or away is None:
            return None
        status, note = _status(data)
        home_result, away_result = _dict(data.get("HomeResult")), _dict(data.get("AwayResult"))
        return cls(
            id=match_id,
            start=parse_datetime(data.get("MatchDateTime")),
            status=status,
            status_note=note,
            home=home,
            away=away,
            venue=_str(data.get("Location")),
            pool=Pool.from_api(data.get("Pool")),
            number=_int(data.get("ExternalMatchId")),
            score_home=_int(home_result.get("Score")),
            score_away=_int(away_result.get("Score")),
            extra_time_home=_int(home_result.get("ExtensionScore")),
            extra_time_away=_int(away_result.get("ExtensionScore")),
            penalties_home=_int(home_result.get("PenaltyScore")),
            penalties_away=_int(away_result.get("PenaltyScore")),
        )

    def involves(self, team_id: str) -> bool:
        """Return whether the team plays in this match."""
        return team_id in (self.home.id, self.away.id)

    def is_home(self, team_id: str) -> bool:
        """Return whether the team plays at home."""
        return self.home.id == team_id

    def opponent(self, team_id: str) -> TeamRef:
        """Return the other team."""
        return self.away if self.is_home(team_id) else self.home

    @property
    def has_score(self) -> bool:
        """Return whether both scores are known."""
        return self.score_home is not None and self.score_away is not None

    @property
    def has_result(self) -> bool:
        """Return whether the match was played and its score entered."""
        return self.status is MatchStatus.FINAL and self.has_score

    def goals(self, team_id: str) -> tuple[int, int] | None:
        """Return (for, against) from the team's point of view, after extra time."""
        if self.score_home is None or self.score_away is None:
            return None
        home = self.extra_time_home if self.extra_time_home is not None else self.score_home
        away = self.extra_time_away if self.extra_time_away is not None else self.score_away
        return (home, away) if self.is_home(team_id) else (away, home)

    def merge(self, newer: Match) -> Match:
        """Combine what a programme and a result know about the same match."""
        return replace(
            newer,
            venue=newer.venue or self.venue,
            pool=newer.pool or self.pool,
            start=newer.start or self.start,
            number=newer.number or self.number,
        )


def parse_program(answer: Any) -> list[Match]:
    """Parse team/TeamProgram: upcoming matches of the team's pools."""
    return [
        match
        for item in _list(_dict(answer).get("ProgramItemMatch"))
        if (match := Match.from_api(_dict(item).get("Match"))) is not None
    ]


def parse_club_program(answer: Any) -> list[Match]:
    """Parse club/ClubProgram: the club's upcoming matches."""
    return [
        match
        for item in _list(_dict(answer).get("ProgramItemMatchClub"))
        if (match := Match.from_api(_dict(item).get("Match"))) is not None
    ]


def parse_club_results(answer: Any) -> list[Match]:
    """Parse club/ClubMatchResults: the club's latest results."""
    return _results(_dict(answer).get("MatchResult"))


def _results(items: Any) -> list[Match]:
    return [match for item in _list(items) if (match := Match.from_api(item)) is not None]


@dataclass(frozen=True, slots=True)
class TeamCompetition:
    """team/TeamCompetitionData: the team's pools, and results outside a pool."""

    pools: list[Pool]
    other_results: list[Match]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TeamCompetition:
        """Parse the answer."""
        pools = [pool for item in _list(data.get("Pool")) if (pool := Pool.from_api(item))]
        other: list[Match] = []
        for key in ("UnboundMatchResults", "SportlinkClubMatchResults", "TournamentMatchResults"):
            other.extend(_results(_dict(data.get(key)).get("MatchResult")))
        return cls(pools=pools, other_results=other)

    def current_pool(self) -> Pool | None:
        """Return the pool with the next match, or else the one played in last."""
        upcoming = [pool for pool in self.pools if pool.next_match is not None]
        if upcoming:
            return min(upcoming, key=lambda pool: pool.next_match or datetime.max)
        played = [pool for pool in self.pools if pool.previous_match is not None]
        if played:
            return max(played, key=lambda pool: pool.previous_match or datetime.min)
        return self.pools[-1] if self.pools else None


@dataclass(frozen=True, slots=True)
class StandingRow:
    """A team's row in a pool's standing."""

    position: int | None
    team: TeamRef
    points: int | None
    played: int | None
    won: int | None
    drawn: int | None
    lost: int | None
    goals_for: int | None
    goals_against: int | None
    goal_difference: int | None
    penalty_points: int | None

    @classmethod
    def from_api(cls, data: Any) -> StandingRow | None:
        """Parse a row; None without a team."""
        data = _dict(data)
        team = TeamRef.from_api(data)
        if team is None:
            return None
        return cls(
            position=_int(data.get("Position")),
            team=team,
            points=_int(data.get("TotalPoints")),
            played=_int(data.get("TotalMatches")),
            won=_int(data.get("Won")),
            drawn=_int(data.get("Draw")),
            lost=_int(data.get("Lost")),
            goals_for=_int(data.get("GoalsFor")),
            goals_against=_int(data.get("GoalsAgainst")),
            goal_difference=_int(data.get("GoalsDifference")),
            penalty_points=_int(data.get("PenaltyPoints")),
        )


@dataclass(frozen=True, slots=True)
class PoolData:
    """pool/PoolCompetitionData: all results of a pool and its standing."""

    results: list[Match] = field(default_factory=list)
    standings: list[StandingRow] = field(default_factory=list)
    # Period standings (periodestanden) of senior pools: period number -> standing.
    periods: dict[int, list[StandingRow]] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> PoolData:
        """Parse the answer."""
        periods: dict[int, list[StandingRow]] = {}
        for item in _list(data.get("PeriodStanding")):
            if (number := _int(_dict(item).get("PeriodNumber"))) is not None:
                periods[number] = _standing(_dict(item).get("PoolStanding"))
        return cls(
            results=_results(data.get("MatchResult")),
            standings=_standing(data.get("PoolStanding")),
            periods=periods,
        )


def _standing(data: Any) -> list[StandingRow]:
    rows = _list(_dict(data).get("PoolStandingTeam"))
    standings = [row for item in rows if (row := StandingRow.from_api(item)) is not None]
    standings.sort(key=lambda row: (row.position is None, row.position or 0))
    return standings


@dataclass(frozen=True, slots=True)
class Facility:
    """match/MatchFacility: where a match is played. The venue's phone is not kept."""

    name: str | None = None
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    pitch: str | None = None
    home_dressing_room: str | None = None
    away_dressing_room: str | None = None
    departure_time: str | None = None
    assembly_time: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Facility:
        """Parse the answer."""
        return cls(
            name=_str(data.get("FacilityName")),
            address=_str(data.get("Address")),
            postal_code=_str(data.get("ZipCode")),
            city=_city(data),
            pitch=_str(data.get("SubFacilityName")),
            home_dressing_room=_str(data.get("HomeDressingRoom")),
            away_dressing_room=_str(data.get("AwayDressingRoom")),
            departure_time=_clock(data.get("DepartureTime")),
            assembly_time=_clock(data.get("AssemblyTime")),
        )

    @property
    def full_address(self) -> str | None:
        """Return `Street 1, 1234 AB Town`."""
        postal = self.postal_code
        if postal and len(postal) == 6 and postal[:4].isdigit():
            postal = f"{postal[:4]} {postal[4:]}"
        town = " ".join(part for part in (postal, self.city) if part)
        return ", ".join(part for part in (self.address, town) if part) or None


def _clock(value: Any) -> str | None:
    """Return a time of day as `HH:MM` (the API sends `09:20:00`)."""
    text = _str(value)
    if text and len(text) == 8 and text.endswith(":00"):
        return text[:5]
    return text


def parse_playing_time(data: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return (net, gross) playing time in minutes from match/MatchInfo.

    Net is the time on the clock (90 for seniors, 60 for under-13s); gross is the
    slot the pitch is booked for.
    """
    attributes = _dict(data.get("ClassExtraAttributes"))
    return _int(attributes.get("NetPlayingTime")), _int(attributes.get("GrossPlayingTime"))
