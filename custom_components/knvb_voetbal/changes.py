"""Detect what changed about a team's matches between two updates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .models import Match, MatchStatus

# Forget matches this long after their start: nothing about them changes any more.
SNAPSHOT_KEEP = timedelta(days=60)


class ChangeType(StrEnum):
    """What happened to a match."""

    CANCELLED = "cancelled"
    REINSTATED = "reinstated"
    RESCHEDULED = "rescheduled"
    LOCATION_CHANGED = "location_changed"
    RESULT = "result"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class MatchState:
    """What we remember of a match to notice changes."""

    status: str
    start: str | None
    venue: str | None
    pitch: str | None
    score: tuple[int, int] | None

    @classmethod
    def of(cls, match: Match, pitch: str | None) -> MatchState:
        """Return the state of a match (the pitch comes from the venue details)."""
        score = None
        if match.has_result:
            assert match.score_home is not None and match.score_away is not None
            score = (match.score_home, match.score_away)
        return cls(
            status=match.status.value,
            start=match.start.isoformat() if match.start else None,
            venue=match.venue,
            pitch=pitch,
            score=score,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-able dict, for storage."""
        return {
            "status": self.status,
            "start": self.start,
            "venue": self.venue,
            "pitch": self.pitch,
            "score": list(self.score) if self.score else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MatchState | None:
        """Read a stored state; None if it is not usable."""
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            return None
        score = data.get("score")
        valid = isinstance(score, list) and len(score) == 2
        return cls(
            status=data["status"],
            start=_text(data.get("start")),
            venue=_text(data.get("venue")),
            pitch=_text(data.get("pitch")),
            score=(score[0], score[1])
            if valid and all(isinstance(v, int) for v in score)
            else None,
        )


@dataclass(frozen=True, slots=True)
class MatchChange:
    """A change to a match."""

    type: ChangeType
    match: Match
    previous: MatchState


def detect_changes(
    previous: dict[str, MatchState], current: Iterable[tuple[Match, MatchState]]
) -> list[MatchChange]:
    """Return the changes between remembered states and the current matches.

    A match not seen before is not a change. A cancellation is reported alone; a venue
    or pitch being known for the first time is not a change, a different one is.
    """
    changes: list[MatchChange] = []
    for match, now in current:
        if (before := previous.get(match.id)) is None:
            continue
        was_cancelled = before.status == MatchStatus.CANCELLED
        is_cancelled = match.status is MatchStatus.CANCELLED
        if is_cancelled and not was_cancelled:
            changes.append(MatchChange(ChangeType.CANCELLED, match, before))
            continue
        if is_cancelled:
            continue
        if was_cancelled:
            changes.append(MatchChange(ChangeType.REINSTATED, match, before))
        elif before.start and now.start and before.start != now.start:
            changes.append(MatchChange(ChangeType.RESCHEDULED, match, before))
        moved = (before.venue and now.venue and before.venue != now.venue) or (
            before.pitch and now.pitch and before.pitch != now.pitch
        )
        if moved and not match.has_result:
            changes.append(MatchChange(ChangeType.LOCATION_CHANGED, match, before))
        if now.score is not None and now.score != before.score:
            changes.append(MatchChange(ChangeType.RESULT, match, before))
    return changes


def updated_states(
    previous: dict[str, MatchState],
    current: Iterable[tuple[Match, MatchState]],
    now: datetime,
) -> dict[str, MatchState]:
    """Return the remembered states with the current ones merged in, minus long-past ones.

    A venue or pitch that is no longer reported (details are only fetched for the
    coming week) keeps its remembered value.
    """
    states = dict(previous)
    for match, seen in current:
        before = states.get(match.id)
        states[match.id] = (
            MatchState(
                status=seen.status,
                start=seen.start,
                venue=seen.venue or before.venue,
                pitch=seen.pitch or before.pitch,
                score=seen.score,
            )
            if before is not None
            else seen
        )
    cutoff = now - SNAPSHOT_KEEP
    return {key: state for key, state in states.items() if not _before(state.start, cutoff)}


def _before(value: str | None, moment: datetime) -> bool:
    if value is None:
        return False
    try:
        return datetime.fromisoformat(value) < moment
    except ValueError:
        return True
