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
    BAND_ALERT,
    BAND_DONE,
    BAND_WORKING,
    FLASH_SECONDS,
    MODE_SLOT_BAND,
    SLOT_EMPTY,
    Slot,
    SlotAllocator,
    slot_band,
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
    event_name: str = "Stop",
) -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id=key,
        display_name=key,
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
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
        self.assertIs(slot_band(AgentMode.BLOCKED_ERROR), BAND_ALERT)
        self.assertIs(slot_band(AgentMode.WAITING_FOR_INPUT), BAND_ALERT)

    def test_working_modes_share_one_rank_and_one_band(self) -> None:
        for mode in (
            AgentMode.TOOL_RUNNING,
            AgentMode.WORKING,
            AgentMode.LONG_TASK_PROGRESS,
        ):
            self.assertEqual(slot_rank(mode), 2)
            self.assertIs(slot_band(mode), BAND_WORKING)

    def test_completed_outranks_idle_though_both_are_green(self) -> None:
        """The ranks are for eviction only; these two render identically."""
        self.assertEqual(slot_rank(AgentMode.COMPLETED), 3)
        self.assertEqual(slot_rank(AgentMode.IDLE_READY), 4)
        self.assertIs(slot_band(AgentMode.COMPLETED), BAND_DONE)
        self.assertIs(slot_band(AgentMode.IDLE_READY), BAND_DONE)

    def test_unknown_holds_no_slot(self) -> None:
        self.assertIsNone(slot_rank(AgentMode.UNKNOWN))
        self.assertIsNone(slot_band(AgentMode.UNKNOWN))

    def test_only_the_working_band_never_flashes(self) -> None:
        """A None peak is what replaced FLASHING_RANKS.

        Derived from ``MODE_SLOT_BAND`` rather than from the three named
        constants, so a fourth band added later has to account for itself here
        instead of slipping past a hardcoded list.
        """
        bands = set(MODE_SLOT_BAND.values())

        self.assertEqual(
            {band for band in bands if band.flash_peak is None}, {BAND_WORKING}
        )
        self.assertEqual(
            {band for band in bands if band.flash_peak is not None},
            {BAND_ALERT, BAND_DONE},
        )

    def test_every_flashing_band_peaks_brighter_than_it_settles(self) -> None:
        """No channel may fall below the baseline, and the peak must be brighter.

        ``pulse`` interpolates per channel, so "brighter overall" is not enough:
        a peak that raises red while dropping blue would still dip that channel
        below the settled colour on every blink, which is the one thing the
        pulse-up shape exists to rule out. Hence the per-channel floor as well as
        the total.

        The hex values are provisional and expected to be re-tuned on hardware.
        This is the invariant that must survive the tuning, so the band set is
        derived rather than listed.
        """
        flashing = [
            band
            for band in set(MODE_SLOT_BAND.values())
            if band.flash_peak is not None
        ]
        self.assertTrue(flashing)

        for band in flashing:
            with self.subTest(band=band):
                for channel in (1, 3, 5):
                    self.assertGreaterEqual(
                        int(band.flash_peak[channel:channel + 2], 16),
                        int(band.steady[channel:channel + 2], 16),
                    )
                steady = sum(int(band.steady[index:index + 2], 16) for index in (1, 3, 5))
                peak = sum(int(band.flash_peak[index:index + 2], 16) for index in (1, 3, 5))
                self.assertLess(steady, peak)


class SlotShapeTests(unittest.TestCase):
    def test_the_flash_fields_are_keyword_only(self) -> None:
        """Positional construction has to raise, not silently render ``0:True``.

        Argument two used to be a ``bool``, and nothing else would catch a
        refactor that dropped ``kw_only``: a stale positional ``True`` stays
        truthy, so ``.flashing`` would read correctly while the renderer emitted
        ``0:True 900ms pulse``.
        """
        with self.assertRaises(TypeError):
            Slot("#000000", "#FF0000")

        self.assertEqual(Slot("#000000", flash_color="#FF0000").flash_color, "#FF0000")


class SlotAssignmentTests(unittest.TestCase):
    def test_a_new_session_takes_a_random_free_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.WORKING),), BASE)

        self.assertEqual(allocator.slot_for_key("a"), 7)
        self.assertEqual(slots[7].color, BAND_WORKING.steady)
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
        self.assertEqual(slots[7].color, BAND_ALERT.steady)

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
        self.assertEqual(slots[vacated].color, BAND_DONE.steady)

    def test_an_unknown_mode_is_never_given_a_slot(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.UNKNOWN),), BASE)

        self.assertIsNone(allocator.slot_for_key("a"))
        self.assertEqual([slot.color for slot in slots], [SLOT_EMPTY] * 8)


class RecordingRandom(random.Random):
    """A ``Random`` that records every candidate list and picks a fixed position.

    Seeded placement pins where one session lands. It cannot show *which* slots
    were on offer, and that is the whole content of the spacing rule -- so these
    tests read the candidate lists instead of a drawn index.
    """

    def __init__(self, pick: int = 0) -> None:
        super().__init__(0)
        self.pick = pick
        self.candidates: list[list[int]] = []

    def choice(self, seq):  # type: ignore[override]
        offered = list(seq)
        self.candidates.append(offered)
        return offered[self.pick]


class SlotSpacingTests(unittest.TestCase):
    """A newcomer takes the free slot farthest from the nearest occupied one.

    Distance is the absolute difference of two indexes. The strip is a line, so it
    does not wrap from slot 7 to slot 0.
    """

    def test_the_first_session_may_land_anywhere(self) -> None:
        """An empty strip offers every slot, which keeps the seeded tests honest."""
        rng = RecordingRandom()
        allocator = SlotAllocator(slot_count=8, rng=rng)

        allocator.update((status("a", AgentMode.WORKING),), BASE)

        self.assertEqual(rng.candidates, [[0, 1, 2, 3, 4, 5, 6, 7]])

    def test_a_newcomer_takes_the_far_end_of_the_strip(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)
        self.assertEqual(allocator.slot_for_key("a"), 7)

        later = BASE + timedelta(seconds=5)
        allocator.update(
            (
                status("a", AgentMode.WORKING),
                status("b", AgentMode.WORKING, updated_at=later),
            ),
            later,
        )

        self.assertEqual(allocator.slot_for_key("b"), 0)

    def test_two_new_sessions_land_at_least_four_slots_apart(self) -> None:
        """Slots 3 and 4 are the worst openers, and each leaves a free slot at 4."""
        for seed in range(12):
            with self.subTest(seed=seed):
                allocator = SlotAllocator(slot_count=8, rng=random.Random(seed))
                allocator.update((status("a", AgentMode.WORKING),), BASE)
                later = BASE + timedelta(seconds=5)
                allocator.update(
                    (
                        status("a", AgentMode.WORKING),
                        status("b", AgentMode.WORKING, updated_at=later),
                    ),
                    later,
                )

                first = allocator.slot_for_key("a")
                second = allocator.slot_for_key("b")

                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                self.assertGreaterEqual(abs(first - second), 4)

    def test_a_third_session_splits_the_remaining_gap(self) -> None:
        """Slots 0 and 7 taken leaves slots 3 and 4 tied on distance 3.

        Both must be on offer. A rule that returned one of them would left-pack the
        middle of the strip.
        """
        for pick, expected in ((0, 3), (-1, 4)):
            with self.subTest(pick=pick):
                rng = RecordingRandom(pick)
                allocator = SlotAllocator(slot_count=8, rng=rng)
                first = status("a", AgentMode.WORKING)
                second = status("b", AgentMode.WORKING)
                third = status("c", AgentMode.WORKING)
                allocator.update((first,), BASE)
                allocator.update((first, second), BASE)

                allocator.update((first, second, third), BASE)

                self.assertEqual(rng.candidates[-1], [3, 4])
                self.assertEqual(allocator.slot_for_key("c"), expected)

    def test_an_occupant_keeps_its_index_when_a_newcomer_arrives(self) -> None:
        """Spacing applies to an assignment. The strip never re-spaces itself."""
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)

        later = BASE + timedelta(seconds=5)
        allocator.update(
            (
                status("a", AgentMode.WORKING),
                status("b", AgentMode.WORKING, updated_at=later),
            ),
            later,
        )

        self.assertEqual(allocator.slot_for_key("a"), 7)


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
        self.assertEqual(slots[idle_slot].color, BAND_ALERT.steady)

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
        self.assertEqual([slot.color for slot in slots], [BAND_ALERT.steady] * 8)

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
        self.assertEqual(slots[7].color, BAND_ALERT.steady)
        self.assertEqual(slots[7].flash_color, BAND_ALERT.flash_peak)
        self.assertAlmostEqual(slots[7].remaining_flash_seconds, FLASH_SECONDS)

    def test_a_slot_stops_flashing_once_the_window_passes(self) -> None:
        """Settling drops the peak. The steady colour was already what line 1 held."""
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WAITING_FOR_INPUT),), BASE)

        settled = BASE + timedelta(seconds=FLASH_SECONDS + 1)
        slots = allocator.update((status("a", AgentMode.WAITING_FOR_INPUT),), settled)

        self.assertFalse(slots[7].flashing)
        self.assertIsNone(slots[7].flash_color)
        self.assertEqual(slots[7].remaining_flash_seconds, 0.0)
        self.assertEqual(slots[7].color, BAND_ALERT.steady)

    def test_the_flash_clock_survives_a_state_change_inside_one_band(self) -> None:
        """BLOCKED_ERROR and WAITING_FOR_INPUT are one band, so the clock runs on."""
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.BLOCKED_ERROR),), BASE)

        later = BASE + timedelta(seconds=60)
        slots = allocator.update(
            (status("a", AgentMode.WAITING_FOR_INPUT, updated_at=later),), later
        )

        self.assertEqual(slots[7].color, BAND_ALERT.steady)
        self.assertTrue(slots[7].flashing)
        self.assertAlmostEqual(slots[7].remaining_flash_seconds, FLASH_SECONDS - 60)

    def test_the_working_bands_clock_survives_working_to_tool_running(self) -> None:
        """The same guarantee for the one pair where Slot cannot show it.

        BAND_WORKING has no flash_peak, so a working slot renders
        remaining_flash_seconds = 0.0 at any age and the clock behind it is
        invisible from outside. Reading the occupant directly is the only way to
        check that WORKING -> TOOL_RUNNING does not restart it. Private, and worth
        it: the alternative is an asserted invariant nobody verifies.
        """
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.WORKING),), BASE)
        entered_at = allocator._slots[7].entered_at

        later = BASE + timedelta(seconds=60)
        allocator.update((status("a", AgentMode.TOOL_RUNNING, updated_at=later),), later)

        self.assertEqual(allocator._slots[7].entered_at, entered_at)
        self.assertEqual(allocator._slots[7].band, BAND_WORKING)

    def test_the_flash_clock_resets_when_the_band_changes(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))
        allocator.update((status("a", AgentMode.COMPLETED),), BASE)

        later = BASE + timedelta(seconds=60)
        slots = allocator.update(
            (status("a", AgentMode.WAITING_FOR_INPUT, updated_at=later),), later
        )

        self.assertEqual(slots[7].color, BAND_ALERT.steady)
        self.assertEqual(slots[7].flash_color, BAND_ALERT.flash_peak)
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

        self.assertEqual(slots[7].color, BAND_ALERT.steady)
        self.assertFalse(slots[7].flashing)

    def test_the_working_band_never_flashes_at_any_age(self) -> None:
        """Working is the boring state. There is no cache-warmth story to signal.

        The five-minute threshold tracks prompt cache TTL: flashing means picking
        this session up now is cheap. An actively running agent has nothing for a
        settle to say, so blue holds one colour from the first frame.
        """
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        for offset in (0.0, 1.0, FLASH_SECONDS - 1, FLASH_SECONDS + 1):
            with self.subTest(offset=offset):
                slots = allocator.update(
                    (status("a", AgentMode.WORKING),), BASE + timedelta(seconds=offset)
                )
                self.assertEqual(slots[7].color, BAND_WORKING.steady)
                self.assertFalse(slots[7].flashing)
                self.assertIsNone(slots[7].flash_color)
                self.assertEqual(slots[7].remaining_flash_seconds, 0.0)

    def test_idle_flashes_green_like_completed(self) -> None:
        """Idle and completed share a band, so they share the flash.

        In practice an idle slot will almost never appear -- IDLE_VISIBLE_SECONDS
        is 0.0, so an idle status is stale by the collection after the one that
        created it. Rank 4 exists to keep the eviction ordering total.
        """
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((status("a", AgentMode.IDLE_READY),), BASE)

        self.assertEqual(slots[7].color, BAND_DONE.steady)
        self.assertEqual(slots[7].flash_color, BAND_DONE.flash_peak)
        self.assertTrue(slots[7].flashing)

    def test_empty_slots_are_black_and_never_flash(self) -> None:
        allocator = SlotAllocator(slot_count=8, rng=random.Random(1234))

        slots = allocator.update((), BASE)

        self.assertEqual(len(slots), 8)
        for slot in slots:
            self.assertEqual(slot.color, SLOT_EMPTY)
            self.assertFalse(slot.flashing)
            self.assertIsNone(slot.flash_color)


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
            sorted(slot.color for slot in slots),
            sorted([BAND_ALERT.steady, BAND_WORKING.steady]),
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
            sorted([BAND_WORKING.steady, BAND_DONE.steady]),
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
            [slot.color for slot in slots if slot.color != SLOT_EMPTY], [BAND_ALERT.steady]
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


class EndedSessionFeedTests(unittest.TestCase):
    """A session that reported ``SessionEnd`` holds no slot.

    Exiting a window is the one signal that says the LED behind it is finished
    being useful, so the strip gives that slot up at once rather than holding it
    for ``COMPLETED_VISIBLE_SECONDS``. The filter is session-keyed rather than
    status-keyed, because a sub-agent status outranks its parent's ``SessionEnd``
    on both sides of the coalesce step.
    """

    def test_an_ended_session_gives_up_its_slot(self) -> None:
        snap = snapshot(
            status(
                "claude:session:gone",
                AgentMode.COMPLETED,
                session_id="gone",
                event_name="SessionEnd",
            ),
            status("claude:session:busy", AgentMode.WORKING, session_id="busy"),
        )

        keys = {item.agent_id for item in multiagent_statuses(snap)}

        self.assertEqual(keys, {"claude:session:busy"})

    def test_an_ended_session_takes_its_waiting_subagent_off_the_strip(self) -> None:
        """The sub-agent outlives the exit by 40 minutes, and must not relight the slot.

        A parent's ``SessionEnd`` status reaches the display list only inside
        ``COMPLETED_VISIBLE_SECONDS``, while its child stays displayable for
        ``stale_after_seconds`` -- an hour in this snapshot. Collect the ended keys
        from the display list rather than from every status the snapshot holds, and
        the filter stops firing at the 20 minute mark: the slot goes dark, then
        comes back on the alert band, at a different index, for a session that no
        longer exists. The 60 second offset alone passes with that bug present.
        """
        parent = status(
            "claude:session:gone",
            AgentMode.COMPLETED,
            session_id="gone",
            event_name="SessionEnd",
        )
        child = status(
            "claude:agent:ac7f1721ec697b403",
            AgentMode.WAITING_FOR_INPUT,
            session_id="gone",
            updated_at=BASE - timedelta(seconds=60),
        )

        for offset in (60.0, COMPLETED_VISIBLE_SECONDS + 60.0, 45 * 60.0):
            with self.subTest(seconds_after_exit=offset):
                snap = snapshot(
                    parent, child, collected_at=BASE + timedelta(seconds=offset)
                )

                self.assertEqual(multiagent_statuses(snap), ())

    def test_a_session_end_with_no_session_id_leaves_other_slots_alone(self) -> None:
        """``session_coalesce_key`` is None here, and None must not match anything.

        A None key inside the ended set removes every session-id-less status on the
        strip, so one exit would darken an unrelated working session.
        """
        snap = snapshot(
            status(
                "grok:unknown",
                AgentMode.COMPLETED,
                event_name="SessionEnd",
            ),
            status("codex:unknown", AgentMode.WORKING),
        )

        keys = {item.agent_id for item in multiagent_statuses(snap)}

        self.assertEqual(keys, {"codex:unknown"})

    def test_a_completed_session_that_never_ended_keeps_its_window(self) -> None:
        """The backstop for a crash and a killed terminal, which send no ``SessionEnd``."""
        snap = snapshot(
            status("claude:session:done", AgentMode.COMPLETED, session_id="done"),
            collected_at=BASE + timedelta(seconds=COMPLETED_VISIBLE_SECONDS - 60),
        )

        keys = {item.agent_id for item in multiagent_statuses(snap)}

        self.assertEqual(keys, {"claude:session:done"})


if __name__ == "__main__":
    unittest.main()
