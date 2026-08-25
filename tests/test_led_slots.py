"""Unit tests for the multiagent LED slot allocator.

Pure state plus arithmetic: no device, no AppKit, no wall clock. Every test
passes ``now`` explicitly and seeds the RNG, so slot placement is exact rather
than merely plausible.
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone

from sidepulse.collector import (
    COMPLETED_VISIBLE_SECONDS,
    IDLE_VISIBLE_SECONDS,
    multiagent_statuses,
    snapshot_from_statuses,
)
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
    session_id: str | None = None,
) -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id=key,
        display_name=key,
        mode=mode,
        updated_at=updated_at,
        event_name="Stop",
        session_id=session_id,
    )


def snapshot(*statuses: AgentStatus, collected_at: datetime = BASE):
    return snapshot_from_statuses(
        statuses,
        collected_at=collected_at,
        sources=(),
        stale_after_seconds=60 * 60.0,
        tool_running_timeout_seconds=10 * 60.0,
        completed_visible_seconds=COMPLETED_VISIBLE_SECONDS,
        idle_visible_seconds=IDLE_VISIBLE_SECONDS,
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


class MultiagentStatusFeedTests(unittest.TestCase):
    """What reaches the allocator, as opposed to what the allocator does with it.

    ``snapshot.statuses`` is the aggregate indicator's set, and it deliberately
    hides finished sessions while anything is active. A strip has room for both,
    so the feed is not the same set.
    """

    def test_completed_sessions_keep_their_slot_while_another_is_active(self) -> None:
        snap = snapshot(
            status("done-a", AgentMode.COMPLETED),
            status("done-b", AgentMode.COMPLETED),
            status("done-c", AgentMode.COMPLETED),
            status("busy", AgentMode.TOOL_RUNNING),
        )

        keys = {status.agent_id for status in multiagent_statuses(snap)}

        self.assertEqual(keys, {"done-a", "done-b", "done-c", "busy"})

    def test_a_completed_session_drops_out_once_it_ages_past_its_window(self) -> None:
        snap = snapshot(
            status(
                "old",
                AgentMode.COMPLETED,
                updated_at=BASE - timedelta(seconds=COMPLETED_VISIBLE_SECONDS + 1),
            ),
            status("busy", AgentMode.WORKING),
        )

        keys = {status.agent_id for status in multiagent_statuses(snap)}

        self.assertEqual(keys, {"busy"})

    def test_idle_sessions_stay_out_of_the_feed(self) -> None:
        """IDLE_VISIBLE_SECONDS is 0, so an idle status is stale on arrival."""
        snap = snapshot(
            status("idle", AgentMode.IDLE_READY),
            status("busy", AgentMode.WORKING),
        )

        keys = {status.agent_id for status in multiagent_statuses(snap)}

        self.assertEqual(keys, {"busy"})

    def test_a_completed_session_renders_green_beside_a_working_one(self) -> None:
        snap = snapshot(
            status("done", AgentMode.COMPLETED),
            status("busy", AgentMode.TOOL_RUNNING),
        )
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update(multiagent_statuses(snap), BASE)

        self.assertEqual(
            sorted(slot.color for slot in slots if slot.color != SLOT_EMPTY),
            sorted([SLOT_CYAN, SLOT_GREEN]),
        )

    def test_a_subagent_shares_its_parent_session_slot(self) -> None:
        snap = snapshot(
            status(
                "claude:session:d3f1",
                AgentMode.WORKING,
                session_id="d3f1",
            ),
            status(
                "claude:agent:ac7f1721ec697b403",
                AgentMode.TOOL_RUNNING,
                session_id="d3f1",
            ),
        )

        statuses = multiagent_statuses(snap)

        self.assertEqual(len(statuses), 1)

    def test_a_subagent_lends_its_parent_the_more_important_mode(self) -> None:
        """A child waiting on a permission prompt has to surface on the parent's slot."""
        snap = snapshot(
            status(
                "claude:session:d3f1",
                AgentMode.WORKING,
                session_id="d3f1",
            ),
            status(
                "claude:agent:ac7f1721ec697b403",
                AgentMode.WAITING_FOR_INPUT,
                session_id="d3f1",
            ),
        )
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update(multiagent_statuses(snap), BASE)

        self.assertEqual(
            [slot.color for slot in slots if slot.color != SLOT_EMPTY], [SLOT_AMBER]
        )

    def test_a_parent_wins_a_tie_against_its_own_subagent(self) -> None:
        """Same mode both sides: the top-level session is the one that survives."""
        snap = snapshot(
            status("claude:session:d3f1", AgentMode.WORKING, session_id="d3f1"),
            status("claude:agent:aaa", AgentMode.WORKING, session_id="d3f1"),
        )

        statuses = multiagent_statuses(snap)

        self.assertEqual(
            [status.agent_id for status in statuses], ["claude:session:d3f1"]
        )

    def test_three_parallel_subagents_light_one_slot_not_four(self) -> None:
        snap = snapshot(
            status("claude:session:d3f1", AgentMode.WORKING, session_id="d3f1"),
            status("claude:agent:aaa", AgentMode.WORKING, session_id="d3f1"),
            status("claude:agent:bbb", AgentMode.WORKING, session_id="d3f1"),
            status("claude:agent:ccc", AgentMode.WORKING, session_id="d3f1"),
        )
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update(multiagent_statuses(snap), BASE)

        self.assertEqual(sum(1 for slot in slots if slot.color != SLOT_EMPTY), 1)

    def test_separate_sessions_keep_separate_slots(self) -> None:
        snap = snapshot(
            status("claude:session:aaa", AgentMode.WORKING, session_id="aaa"),
            status("claude:session:bbb", AgentMode.WORKING, session_id="bbb"),
        )

        statuses = multiagent_statuses(snap)

        self.assertEqual(len(statuses), 2)

    def test_a_session_without_a_session_id_keeps_its_own_slot(self) -> None:
        """No session_id means nothing to coalesce on, so it must pass through."""
        snap = snapshot(
            status("codex:unknown", AgentMode.WORKING),
            status("claude:session:aaa", AgentMode.WORKING, session_id="aaa"),
        )

        statuses = multiagent_statuses(snap)

        self.assertEqual(len(statuses), 2)

    def test_same_session_id_across_providers_stays_separate(self) -> None:
        grok = status("grok:session:shared", AgentMode.WORKING, session_id="shared")
        snap = snapshot(
            status("claude:session:shared", AgentMode.WORKING, session_id="shared"),
            AgentStatus(**{**grok.__dict__, "provider": "grok"}),
        )

        statuses = multiagent_statuses(snap)

        self.assertEqual(len(statuses), 2)


if __name__ == "__main__":
    unittest.main()
