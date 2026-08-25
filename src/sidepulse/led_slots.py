"""Slot allocation for the multiagent LED display.

One LED is one top-level agent session. A session takes a slot when it becomes
displayable, keeps it while it stays displayable, and gives it up when it drops
out of the fresh-status set. Colour says what the session needs; a slot flashes
for the first five minutes after it enters a colour, so "changed recently" reads
differently from "has been like this a while".

Standard library only, and no wall-clock reads: ``now`` is always passed in.
That keeps the module unit-testable in isolation and keeps it importable with
PyObjC absent.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .models import AgentMode, AgentStatus


SLOT_AMBER = "#FF3A00"
SLOT_CYAN = "#00E5FF"
SLOT_GREEN = "#00FF66"
SLOT_EMPTY = "#000000"

# Five minutes is the prompt cache TTL. Flashing therefore means the cache
# behind that session is probably still warm, so picking it up now is cheap;
# steady means resuming will pay full price. That is why the flash settles
# instead of nagging until answered -- once the cache is cold, the urgency the
# flash was signalling is no longer true.
FLASH_SECONDS = 5 * 60.0

# Rank 1 is most important. Eviction runs off this: a newcomer takes the slot of
# the strictly least important occupant, and only if it outranks that occupant.
MODE_SLOT_RANK: dict[AgentMode, int] = {
    AgentMode.BLOCKED_ERROR: 1,
    AgentMode.WAITING_FOR_INPUT: 1,
    AgentMode.TOOL_RUNNING: 2,
    AgentMode.WORKING: 2,
    AgentMode.LONG_TASK_PROGRESS: 2,
    AgentMode.COMPLETED: 3,
    AgentMode.IDLE_READY: 4,
}

MODE_SLOT_COLOR: dict[AgentMode, str] = {
    AgentMode.BLOCKED_ERROR: SLOT_AMBER,
    AgentMode.WAITING_FOR_INPUT: SLOT_AMBER,
    AgentMode.TOOL_RUNNING: SLOT_CYAN,
    AgentMode.WORKING: SLOT_CYAN,
    AgentMode.LONG_TASK_PROGRESS: SLOT_CYAN,
    AgentMode.COMPLETED: SLOT_GREEN,
    AgentMode.IDLE_READY: SLOT_GREEN,
}

# IDLE_READY (rank 4) holds a slot but never flashes: it means "a session exists
# and has done nothing yet", which is not news. In practice it will almost never
# light a slot at all, because IDLE_VISIBLE_SECONDS is 0.0 and so an idle status
# is stale by the collection after the one that created it. The rank is here so
# the eviction ordering is total.
FLASHING_RANKS = frozenset({1, 2, 3})


def slot_rank(mode: AgentMode) -> int | None:
    """Importance rank, or None for a mode that holds no slot."""
    return MODE_SLOT_RANK.get(mode)


def slot_color(mode: AgentMode) -> str | None:
    """Slot colour, or None for a mode that holds no slot."""
    return MODE_SLOT_COLOR.get(mode)


@dataclass(frozen=True)
class Slot:
    """One rendered LED position."""

    color: str
    flashing: bool = False
    remaining_flash_seconds: float = 0.0


@dataclass
class _Occupant:
    key: str
    rank: int
    color: str
    entered_at: datetime


class SlotAllocator:
    def __init__(
        self,
        *,
        slot_count: int = 8,
        rng: random.Random | None = None,
        flash_seconds: float = FLASH_SECONDS,
    ) -> None:
        self.slot_count = max(1, int(slot_count))
        self.flash_seconds = float(flash_seconds)
        # Injected so tests can seed it and assert exact placement.
        self._rng = rng if rng is not None else random.Random()
        self._slots: list[_Occupant | None] = [None] * self.slot_count

    def reset(self) -> None:
        self._slots = [None] * self.slot_count

    def slot_for_key(self, key: str) -> int | None:
        for index, occupant in enumerate(self._slots):
            if occupant is not None and occupant.key == key:
                return index
        return None

    def update(
        self, statuses: Iterable[AgentStatus], now: datetime
    ) -> tuple[Slot, ...]:
        """Fold the fresh-status set into the slot map and render it."""
        pending = self._displayable(statuses)

        # Release. A slot is freed when its session leaves the fresh-status set,
        # whether it is gone or has aged into stale_statuses.
        for index, occupant in enumerate(self._slots):
            if occupant is not None and occupant.key not in pending:
                self._slots[index] = None

        # Hold. A session keeps its slot across state and colour changes; only a
        # colour change restarts the flash clock, so WORKING -> TOOL_RUNNING
        # (both cyan) leaves it running.
        for occupant in self._slots:
            if occupant is None:
                continue
            rank, color, updated_at = pending.pop(occupant.key)
            occupant.rank = rank
            if color != occupant.color:
                occupant.color = color
                occupant.entered_at = updated_at

        # Assign. Most important newcomer picks first, so a full strip gives the
        # single free slot to the session that most needs it.
        newcomers = sorted(pending.items(), key=lambda item: (item[1][0], item[1][2]))
        for key, (rank, color, updated_at) in newcomers:
            index = self._free_slot()
            if index is None:
                index = self._evictable_slot(rank)
            if index is None:
                # No slot free and nothing less important to displace. This
                # session is simply not shown.
                continue
            self._slots[index] = _Occupant(
                key=key, rank=rank, color=color, entered_at=updated_at
            )

        return self._render(now)

    def _displayable(
        self, statuses: Iterable[AgentStatus]
    ) -> dict[str, tuple[int, str, datetime]]:
        displayable: dict[str, tuple[int, str, datetime]] = {}
        for status in statuses:
            rank = slot_rank(status.mode)
            color = slot_color(status.mode)
            if rank is None or color is None:
                continue
            candidate = (rank, color, status.updated_at)
            existing = displayable.get(status.agent_id)
            if existing is None or candidate[0] < existing[0]:
                displayable[status.agent_id] = candidate
        return displayable

    def _free_slot(self) -> int | None:
        free = [index for index, occupant in enumerate(self._slots) if occupant is None]
        if not free:
            return None
        # Random rather than left-packed: position is arbitrary but sticky, so
        # you learn "my sidepulse session is the third one" by repetition.
        return self._rng.choice(free)

    def _evictable_slot(self, rank: int) -> int | None:
        occupied = [
            (occupant.rank, occupant.entered_at, index)
            for index, occupant in enumerate(self._slots)
            if occupant is not None
        ]
        if not occupied:
            return None
        weakest_rank = max(entry[0] for entry in occupied)
        if rank >= weakest_rank:
            # Not strictly more important than every occupant.
            return None
        tied = [entry for entry in occupied if entry[0] == weakest_rank]
        tied.sort(key=lambda entry: (entry[1], entry[2]))
        return tied[0][2]

    def _render(self, now: datetime) -> tuple[Slot, ...]:
        slots: list[Slot] = []
        for occupant in self._slots:
            if occupant is None:
                slots.append(Slot(color=SLOT_EMPTY))
                continue
            remaining = 0.0
            if occupant.rank in FLASHING_RANKS:
                age = max(0.0, (now - occupant.entered_at).total_seconds())
                remaining = max(0.0, self.flash_seconds - age)
            slots.append(
                Slot(
                    color=occupant.color,
                    flashing=remaining > 0.0,
                    remaining_flash_seconds=remaining,
                )
            )
        return tuple(slots)
