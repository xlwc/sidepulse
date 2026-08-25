"""Unit tests for the multiagent LED slot allocator.

Pure state plus arithmetic: no device, no AppKit, no wall clock. Every test
passes ``now`` explicitly and seeds the RNG, so slot placement is exact rather
than merely plausible.
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone

from sidepulse.led_slots import (
    FLASH_SECONDS,
    SLOT_AMBER,
    SLOT_CYAN,
    SLOT_EMPTY,
    SLOT_GREEN,
    SlotAllocator,
    slot_color,
    slot_rank,
)
from sidepulse.models import AgentMode, AgentStatus


BASE = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)


def status(
    key: str,
    mode: AgentMode,
    *,
    updated_at: datetime = BASE,
) -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id=key,
        display_name=key,
        mode=mode,
        updated_at=updated_at,
        event_name="Stop",
    )


class SlotRankTests(unittest.TestCase):
    def test_blocked_and_waiting_share_the_top_rank(self) -> None:
        self.assertEqual(slot_rank(AgentMode.BLOCKED_ERROR), 1)
        self.assertEqual(slot_rank(AgentMode.WAITING_FOR_INPUT), 1)

    def test_working_modes_share_one_rank_and_one_colour(self) -> None:
        for mode in (
            AgentMode.TOOL_RUNNING,
            AgentMode.WORKING,
            AgentMode.LONG_TASK_PROGRESS,
        ):
            self.assertEqual(slot_rank(mode), 2)
            self.assertEqual(slot_color(mode), SLOT_CYAN)

    def test_completed_outranks_idle_though_both_are_green(self) -> None:
        self.assertEqual(slot_rank(AgentMode.COMPLETED), 3)
        self.assertEqual(slot_rank(AgentMode.IDLE_READY), 4)
        self.assertEqual(slot_color(AgentMode.COMPLETED), SLOT_GREEN)
        self.assertEqual(slot_color(AgentMode.IDLE_READY), SLOT_GREEN)

    def test_unknown_holds_no_slot(self) -> None:
        self.assertIsNone(slot_rank(AgentMode.UNKNOWN))
        self.assertIsNone(slot_color(AgentMode.UNKNOWN))


class SlotAssignmentTests(unittest.TestCase):
    def test_a_new_session_takes_a_random_free_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.WORKING),), BASE)

        self.assertEqual(allocator.slot_for_key("a"), 7)
        self.assertEqual(slots[7].color, SLOT_CYAN)
        self.assertEqual([slot.color for slot in slots[:7]], [SLOT_EMPTY] * 7)

    def test_a_session_holds_its_slot_across_colour_changes(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)

        later = BASE + timedelta(seconds=30)
        slots = allocator.update(
            (status("a", AgentMode.WAITING_FOR_INPUT, updated_at=later),),
            later,
        )

        self.assertEqual(allocator.slot_for_key("a"), 7)
        self.assertEqual(slots[7].color, SLOT_AMBER)

    def test_a_disappearing_session_releases_its_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)
        self.assertEqual(allocator.slot_for_key("a"), 7)

        slots = allocator.update((), BASE + timedelta(seconds=1))

        self.assertIsNone(allocator.slot_for_key("a"))
        self.assertEqual([slot.color for slot in slots], [SLOT_EMPTY] * 8)

    def test_the_freed_slot_rejoins_the_pool(self) -> None:
        """Fill the strip, drop one session, and the newcomer must land in the hole.

        Exactly one slot is free, so this pins the release path without
        depending on which index the RNG would have drawn.
        """
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        held = tuple(status(f"held{index}", AgentMode.WORKING) for index in range(8))
        allocator.update(held, BASE)
        vacated = allocator.slot_for_key("held3")
        self.assertIsNotNone(vacated)

        later = BASE + timedelta(seconds=5)
        slots = allocator.update(
            tuple(item for item in held if item.agent_id != "held3")
            + (status("newcomer", AgentMode.COMPLETED, updated_at=later),),
            later,
        )

        self.assertIsNone(allocator.slot_for_key("held3"))
        self.assertEqual(allocator.slot_for_key("newcomer"), vacated)
        self.assertEqual(slots[vacated].color, SLOT_GREEN)

    def test_an_unknown_mode_is_never_given_a_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.UNKNOWN),), BASE)

        self.assertIsNone(allocator.slot_for_key("a"))
        self.assertEqual([slot.color for slot in slots], [SLOT_EMPTY] * 8)


class SlotEvictionTests(unittest.TestCase):
    def fill(self, allocator: SlotAllocator, mode: AgentMode, count: int = 8) -> None:
        allocator.update(
            tuple(status(f"held{index}", mode) for index in range(count)), BASE
        )

    def test_a_more_important_newcomer_evicts_the_least_important_occupant(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        occupants = [status(f"held{index}", AgentMode.BLOCKED_ERROR) for index in range(7)]
        occupants.append(status("idler", AgentMode.IDLE_READY))
        allocator.update(tuple(occupants), BASE)
        idle_slot = allocator.slot_for_key("idler")
        self.assertIsNotNone(idle_slot)

        later = BASE + timedelta(seconds=5)
        slots = allocator.update(
            tuple(occupants[:7])
            + (status("idler", AgentMode.IDLE_READY),)
            + (status("asker", AgentMode.WAITING_FOR_INPUT, updated_at=later),),
            later,
        )

        self.assertEqual(allocator.slot_for_key("asker"), idle_slot)
        self.assertIsNone(allocator.slot_for_key("idler"))
        self.assertEqual(slots[idle_slot].color, SLOT_AMBER)

    def test_a_less_important_newcomer_gets_no_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        occupants = tuple(
            status(f"held{index}", AgentMode.BLOCKED_ERROR) for index in range(8)
        )
        allocator.update(occupants, BASE)

        later = BASE + timedelta(seconds=5)
        slots = allocator.update(
            occupants + (status("idler", AgentMode.IDLE_READY, updated_at=later),),
            later,
        )

        self.assertIsNone(allocator.slot_for_key("idler"))
        self.assertEqual([slot.color for slot in slots], [SLOT_AMBER] * 8)

    def test_an_equally_important_newcomer_gets_no_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        occupants = tuple(status(f"held{index}", AgentMode.WORKING) for index in range(8))
        allocator.update(occupants, BASE)

        later = BASE + timedelta(seconds=5)
        allocator.update(
            occupants + (status("peer", AgentMode.TOOL_RUNNING, updated_at=later),),
            later,
        )

        self.assertIsNone(allocator.slot_for_key("peer"))

    def test_eviction_ties_break_on_the_oldest_state_entry(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        occupants = tuple(
            status(
                f"held{index}",
                AgentMode.COMPLETED,
                updated_at=BASE + timedelta(seconds=index),
            )
            for index in range(8)
        )
        allocator.update(occupants, BASE + timedelta(seconds=8))
        oldest_slot = allocator.slot_for_key("held0")

        later = BASE + timedelta(seconds=20)
        allocator.update(
            occupants + (status("asker", AgentMode.BLOCKED_ERROR, updated_at=later),),
            later,
        )

        self.assertEqual(allocator.slot_for_key("asker"), oldest_slot)
        self.assertIsNone(allocator.slot_for_key("held0"))
        for index in range(1, 8):
            self.assertIsNotNone(allocator.slot_for_key(f"held{index}"))


class FlashClockTests(unittest.TestCase):
    def test_a_fresh_slot_flashes_with_the_full_window_remaining(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.WAITING_FOR_INPUT),), BASE)

        self.assertTrue(slots[7].flashing)
        self.assertAlmostEqual(slots[7].remaining_flash_seconds, FLASH_SECONDS)

    def test_a_slot_stops_flashing_once_the_window_passes(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WAITING_FOR_INPUT),), BASE)

        settled = BASE + timedelta(seconds=FLASH_SECONDS + 1)
        slots = allocator.update((status("a", AgentMode.WAITING_FOR_INPUT),), settled)

        self.assertFalse(slots[7].flashing)
        self.assertEqual(slots[7].remaining_flash_seconds, 0.0)
        self.assertEqual(slots[7].color, SLOT_AMBER)

    def test_the_flash_clock_survives_a_state_change_inside_one_colour(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)

        later = BASE + timedelta(seconds=60)
        slots = allocator.update(
            (status("a", AgentMode.TOOL_RUNNING, updated_at=later),), later
        )

        self.assertEqual(slots[7].color, SLOT_CYAN)
        self.assertTrue(slots[7].flashing)
        self.assertAlmostEqual(slots[7].remaining_flash_seconds, FLASH_SECONDS - 60)

    def test_the_flash_clock_resets_when_the_colour_changes(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)

        later = BASE + timedelta(seconds=60)
        slots = allocator.update(
            (status("a", AgentMode.WAITING_FOR_INPUT, updated_at=later),), later
        )

        self.assertEqual(slots[7].color, SLOT_AMBER)
        self.assertAlmostEqual(slots[7].remaining_flash_seconds, FLASH_SECONDS)

    def test_the_flash_clock_runs_from_the_status_timestamp_not_first_sight(self) -> None:
        """A session first seen long after it changed state renders settled.

        This is what makes the flash survive an app restart or a sleep/wake
        without lying: the clock is the agent's timestamp, not ours.
        """
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        stale_but_fresh = status("a", AgentMode.WAITING_FOR_INPUT, updated_at=BASE)

        slots = allocator.update(
            (stale_but_fresh,), BASE + timedelta(seconds=FLASH_SECONDS + 30)
        )

        self.assertEqual(slots[7].color, SLOT_AMBER)
        self.assertFalse(slots[7].flashing)

    def test_idle_never_flashes(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.IDLE_READY),), BASE)

        self.assertEqual(slots[7].color, SLOT_GREEN)
        self.assertFalse(slots[7].flashing)
        self.assertEqual(slots[7].remaining_flash_seconds, 0.0)

    def test_empty_slots_are_black_and_never_flash(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((), BASE)

        self.assertEqual(len(slots), 8)
        for slot in slots:
            self.assertEqual(slot.color, SLOT_EMPTY)
            self.assertFalse(slot.flashing)


class SlotCountTests(unittest.TestCase):
    def test_a_two_led_strip_allocates_two_slots(self) -> None:
        allocator = SlotAllocator(slot_count=2, rng=random.Random(1234))

        slots = allocator.update(
            (
                status("a", AgentMode.WORKING),
                status("b", AgentMode.BLOCKED_ERROR),
            ),
            BASE,
        )

        self.assertEqual(len(slots), 2)
        self.assertEqual(
            sorted(slot.color for slot in slots), sorted([SLOT_AMBER, SLOT_CYAN])
        )


if __name__ == "__main__":
    unittest.main()
