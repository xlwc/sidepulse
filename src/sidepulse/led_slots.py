"""Slot allocation for the multiagent LED display.

One LED is one top-level agent session. A session takes a slot when it becomes
displayable, keeps it while it stays displayable, and gives it up when it drops
out of the set it was fed. A colour band says what the session needs; bands that
carry a flash peak blink for the first five minutes after a session enters them
and then settle to their steady colour, so "changed recently" reads differently
from "has been like this a while".

"Top-level" is enforced upstream, in ``collector.multiagent_statuses``, not
here: this module keys on ``AgentStatus.agent_id``, and a Task-tool sub-agent
carries an ``agent_id`` distinct from its parent's. Fold sub-agents in before
calling ``update``, or three parallel children light four LEDs.

Standard library only, and no wall-clock reads: ``now`` is always passed in.
That keeps the module unit-testable in isolation and keeps it importable with
PyObjC absent.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .models import AgentMode, AgentStatus


SLOT_EMPTY = "#000000"

# Five minutes is the prompt cache TTL. Flashing therefore means the cache
# behind that session is probably still warm, so picking it up now is cheap;
# steady means resuming will pay full price. That is why the flash settles
# instead of nagging until answered -- once the cache is cold, the urgency the
# flash was signalling is no longer true.
FLASH_SECONDS = 5 * 60.0


@dataclass(frozen=True)
class SlotBand:
    """What a slot looks like settled, and what it flashes up to.

    ``flash_peak`` is None for a band that never flashes, which is what replaced
    the old rank-based ``FLASHING_RANKS`` set: whether a slot blinks was always a
    property of its colour, not of its eviction priority, and the working band
    having no peak makes that load-bearing.
    """

    steady: str
    flash_peak: str | None

    def slot(self, remaining_flash_seconds: float = 0.0) -> "Slot":
        """Render this band with ``remaining`` seconds of flash window left."""
        flashing = self.flash_peak is not None and remaining_flash_seconds > 0.0
        return Slot(
            color=self.steady,
            flash_color=self.flash_peak if flashing else None,
            remaining_flash_seconds=remaining_flash_seconds if flashing else 0.0,
        )


# Intensity lives in these values rather than in the `brightness` directive,
# which is program-global and so cannot vary per slot. The directive carries the
# user's brightness slider instead, as a master gain over these.
#
# Chosen by eye on a mounted SidePulse Pro on 2026-08-25, one band at a time,
# against hue ramps written to the strip. Numeric intensity is not perceived
# intensity -- one tier fraction does not read as one tier across red, blue and
# green -- which is why the tiers below are uneven and why blue sits above the
# nominal medium. No code outside this table needs to change when you retune, but
# the three exact-program-text tests in tests/test_sidepulse.py and the colour
# table, worked example and two effective-intensity figures in docs/usage.md all
# restate these values. The tests fail loudly; the doc is guarded by the
# hex-parity test in tests/test_packaging.py.
#
# Hue notes, since the secondary channels look like typos and are not. Alert peaks
# one step warm of pure red, chosen over pure red on the strip. Working is pure
# blue with no green at all, so it cannot be confused with either other band, and
# is turned up to compensate for blue contributing least to perceived brightness.
# Done is chartreuse rather than green, and carries red deliberately.
BAND_ALERT = SlotBand(steady="#3D0000", flash_peak="#FF1000")  # low 24% / high 100%
BAND_WORKING = SlotBand(steady="#0000A0", flash_peak=None)  # 63%
BAND_DONE = SlotBand(steady="#1E3D00", flash_peak="#408000")  # low 24% / medium 50%


# Rank 1 is most important. Eviction runs off this: a newcomer takes the slot of
# the strictly least important occupant, and only if it outranks that occupant.
# Ranks 3 and 4 render identically and differ here alone.
MODE_SLOT_RANK: dict[AgentMode, int] = {
    AgentMode.BLOCKED_ERROR: 1,
    AgentMode.WAITING_FOR_INPUT: 1,
    AgentMode.TOOL_RUNNING: 2,
    AgentMode.WORKING: 2,
    AgentMode.LONG_TASK_PROGRESS: 2,
    AgentMode.COMPLETED: 3,
    AgentMode.IDLE_READY: 4,
}

MODE_SLOT_BAND: dict[AgentMode, SlotBand] = {
    AgentMode.BLOCKED_ERROR: BAND_ALERT,
    AgentMode.WAITING_FOR_INPUT: BAND_ALERT,
    AgentMode.TOOL_RUNNING: BAND_WORKING,
    AgentMode.WORKING: BAND_WORKING,
    AgentMode.LONG_TASK_PROGRESS: BAND_WORKING,
    AgentMode.COMPLETED: BAND_DONE,
    AgentMode.IDLE_READY: BAND_DONE,
}


def slot_rank(mode: AgentMode) -> int | None:
    """Importance rank, or None for a mode that holds no slot."""
    return MODE_SLOT_RANK.get(mode)


def slot_band(mode: AgentMode) -> SlotBand | None:
    """Colour band, or None for a mode that holds no slot."""
    return MODE_SLOT_BAND.get(mode)


@dataclass(frozen=True)
class Slot:
    """One rendered LED position.

    ``flash_color`` and ``remaining_flash_seconds`` are keyword-only on purpose.
    Argument two used to be a ``bool``; a stale positional ``True`` would stay
    truthy, so ``.flashing`` would read correctly while the renderer emitted
    ``0:True 900ms pulse`` -- visible only where a test asserts exact program
    text. Keyword-only turns every stale call site into a TypeError instead.
    """

    color: str
    flash_color: str | None = field(default=None, kw_only=True)
    remaining_flash_seconds: float = field(default=0.0, kw_only=True)

    @property
    def flashing(self) -> bool:
        return self.flash_color is not None


EMPTY_SLOT = Slot(SLOT_EMPTY)


@dataclass
class _Occupant:
    key: str
    rank: int
    band: SlotBand
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

    # No reset(): there is no event that should reshuffle the strip. `update`
    # releases occupants that leave the incoming set, so the map self-cleans, and
    # a changed slot count builds a new allocator rather than clearing this one.

    def slot_for_key(self, key: str) -> int | None:
        for index, occupant in enumerate(self._slots):
            if occupant is not None and occupant.key == key:
                return index
        return None

    def update(
        self, statuses: Iterable[AgentStatus], now: datetime
    ) -> tuple[Slot, ...]:
        """Fold the incoming status set into the slot map and render it.

        Feed this ``collector.multiagent_statuses``, not ``snapshot.statuses``:
        the latter hides finished sessions whenever anything is active, which on
        a strip reads as "nothing finished" rather than "one LED, one answer".
        """
        pending = self._displayable(statuses)

        # Release. A slot is freed when its session leaves the incoming set --
        # by ageing out of its visibility window, or by ending.
        for index, occupant in enumerate(self._slots):
            if occupant is not None and occupant.key not in pending:
                self._slots[index] = None

        # Hold. A session keeps its slot across state and band changes; only a
        # band change restarts the flash clock, so WORKING -> TOOL_RUNNING (both
        # working) and BLOCKED_ERROR -> WAITING_FOR_INPUT (both alert) leave it
        # running.
        for occupant in self._slots:
            if occupant is None:
                continue
            rank, band, updated_at = pending.pop(occupant.key)
            occupant.rank = rank
            if band != occupant.band:
                occupant.band = band
                occupant.entered_at = updated_at

        # Assign. Most important newcomer picks first, so a full strip gives the
        # single free slot to the session that most needs it.
        newcomers = sorted(pending.items(), key=lambda item: (item[1][0], item[1][2]))
        for key, (rank, band, updated_at) in newcomers:
            index = self._free_slot()
            if index is None:
                index = self._evictable_slot(rank)
            if index is None:
                # No slot free and nothing less important to displace. This
                # session is simply not shown.
                continue
            self._slots[index] = _Occupant(
                key=key, rank=rank, band=band, entered_at=updated_at
            )

        return self._render(now)

    def _displayable(
        self, statuses: Iterable[AgentStatus]
    ) -> dict[str, tuple[int, SlotBand, datetime]]:
        displayable: dict[str, tuple[int, SlotBand, datetime]] = {}
        for status in statuses:
            rank = slot_rank(status.mode)
            band = slot_band(status.mode)
            if rank is None or band is None:
                continue
            candidate = (rank, band, status.updated_at)
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
            # Ties do not displace: the newcomer has to outrank the weakest
            # occupant on the strip, not merely match it.
            return None
        tied = [entry for entry in occupied if entry[0] == weakest_rank]
        tied.sort(key=lambda entry: (entry[1], entry[2]))
        return tied[0][2]

    def _render(self, now: datetime) -> tuple[Slot, ...]:
        slots: list[Slot] = []
        for occupant in self._slots:
            if occupant is None:
                slots.append(EMPTY_SLOT)
                continue
            # No peakless-band guard here: `slot()` zeroes the remaining window
            # for a band that cannot flash, so one decision point is enough.
            age = max(0.0, (now - occupant.entered_at).total_seconds())
            slots.append(occupant.band.slot(max(0.0, self.flash_seconds - age)))
        return tuple(slots)
