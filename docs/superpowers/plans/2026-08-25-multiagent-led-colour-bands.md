# Multiagent LED Colour Bands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multiagent strip's three flat colours with three colour bands carrying their own intensity — red flashes bright and settles dim, blue holds medium and never flashes, green flashes medium and settles dim — and wire the device submenu's brightness slider through as a master gain.

**Architecture:** Intensity moves out of the global `brightness` directive (which is program-global and cannot vary per slot) and into the colour values, expressed as a `SlotBand` dataclass holding a `steady` colour and an optional `flash_peak`. The flash inverts as a consequence: the program's baseline line becomes each slot's *settled* colour and the flash line pulses *up* to the peak, so `repeat` expiry lands on the post-five-minute appearance instead of the pre-settle one. `FLASHING_RANKS` is deleted — whether a slot flashes is now a property of its band, not its eviction rank. Ranks, the allocator's assign/hold/release/evict logic, `AgentMode`, `LedDisplayState`, and `collector.multiagent_statuses` are untouched.

**Tech Stack:** Python 3.10+ (`.venv` here is 3.14.7), `unittest.TestCase` classes run under pytest, PyObjC/JavaScriptCore for the `sdled.wasm` firmware parser via `sidepulse.led_wasm`.

**Spec:** [`docs/superpowers/specs/2026-08-25-multiagent-led-colour-bands-design.md`](../specs/2026-08-25-multiagent-led-colour-bands-design.md)

---

## Before you start

**Use `.venv/bin/python` for everything.** The `python3` on `PATH` in this checkout is 3.9.6 and cannot even import the package. The editable install lives in `.venv` (3.14.7):

```sh
.venv/bin/python -c "import sidepulse; print(sidepulse.__file__)"
# /Users/lawrencechang/src/sidepulse/src/sidepulse/__init__.py
```

Full suite, skipping the ~15 s clean-venv builds:

```sh
SIDEPULSE_SKIP_CLEAN_INSTALL=1 .venv/bin/python -m pytest tests -q
```

**Commit signing may prompt.** `git commit` in this repo is configured to GPG-sign and the signing agent can fail in a non-interactive shell (`error: gpg failed to sign the data`). If a commit step fails that way, write the message to `.git/COMMITMSG` and ask the user to run `git commit -F .git/COMMITMSG` themselves. Do not silently add `--no-gpg-sign`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/sidepulse/led_slots.py` | band table, slot allocation, flash clock | `SlotBand` + three band constants replace `SLOT_AMBER`/`SLOT_CYAN`/`SLOT_GREEN`; `MODE_SLOT_BAND`/`slot_band()` replace `MODE_SLOT_COLOR`/`slot_color()`; `FLASHING_RANKS` deleted; `Slot` gains keyword-only `flash_color` with `.flashing` derived; `_Occupant.color` → `_Occupant.band` |
| `src/sidepulse/led_status.py` | LEDS.LED program text, write dedupe, controllers | flash-line target → `slot.flash_color`; `SLOT_BRIGHTNESS` 128 → 255; `slot_render_key` carries the colour pair |
| `src/sidepulse/status_bar.py` | menu-bar app, per-device controllers | assign `controller.brightness`; un-hide the brightness readout and slider in multiagent mode |
| `src/sidepulse/cli.py` | `agent-monitor leds` | **no change** — its `SLOT_EMPTY` comparison and `.flashing` count both still hold |
| `tests/test_led_slots.py` | allocator and flash-clock unit tests | band assertions; working-never-flashes and idle-does-flash invert |
| `tests/test_sidepulse.py` | renderer, controller, submenu tests | `Slot(...)` call sites, expected program text, brightness tests invert, submenu test inverts |
| `docs/usage.md` | operational guide | colour table, brightness gotcha, "amber" reference, sample program |
| `docs/superpowers/specs/2026-08-24-multiagent-led-slots-design.md` | prior design | superseded-by pointer at the top |

`virtual_device.py` needs no change: it renders the Screen Bar by running program text through `led_wasm`, so it picks up the new bands for free.

---

## Task 1: Confirm the pulse-up shape in the firmware engine

The whole design rests on two properties of the `LEDS.LED` DSL that `LEDS_FORMAT.md` documents but `device_writer.validate_led_text` cannot check, because it only measures size. See them yourself before building on them.

**Files:**
- No files change. This is a measurement.

- [ ] **Step 1: Run the harness against a hand-written pulse-up program**

```sh
.venv/bin/python -c "
from sidepulse.led_wasm import SdLedWasmController
c = SdLedWasmController(led_count=8)
prog = '0:#3D0000 1:#002A80\n0:#FF0000 900ms pulse\nrepeat 2'
c.reset(0)
print('parse:', c.parse(prog, 0))
for t in range(0, 1000, 50):
    px = c.step(t)
    print(t, px[0], px[1])
print('settled@3000:', c.step(3000)[0], c.step(3000)[1])
"
```

Expected — this has already been run once and produced exactly this, so treat a difference as a real problem, not as noise:

```text
parse: LedWasmParseResult(ok=True, error=0, error_name='ok', line=0, column=0)
0 (61, 0, 0) (0, 42, 128)
50 (64, 0, 0) (0, 42, 128)
...
450 (254, 0, 0) (0, 42, 128)
...
900 (62, 0, 0) (0, 42, 128)
950 (62, 0, 0) (0, 42, 128)
settled@3000: (61, 0, 0) (0, 42, 128)
```

What each column proves:

- **Slot 0 rises from 61 to 254 and returns to 61.** `pulse` reaches a *brighter* target from a dim line-start colour and comes back. Red never dips below 61, which is the whole difference from today's pulse-to-black.
- **The peak is 254, not 255.** The envelope does not land exactly on the target at these sample points. Tests must assert `>= 250`, never `== 255`.
- **Slot 1 holds `(0, 42, 128)` at every sample.** `#002A80` is on line 1 and absent from line 2, so it holds rock steady — that is the working band never flashing.
- **`settled@3000` is `(61, 0, 0)`.** Past the repeat window the device holds the baseline, which *is* the settled colour. This is the property that makes a dead status-bar app leave the strip correct instead of frozen bright.

If `SdLedWasmController` raises `LedWasmUnavailableError`, PyObjC/JavaScriptCore is missing from `.venv`. Stop and report that rather than proceeding on documentation alone — this task is the only check that catches DSL mistakes.

- [ ] **Step 2: Record nothing, commit nothing**

There is no artifact. The permanent regression test for this lands in Task 2 Step 8, written against the band constants rather than against these literals so that tuning the hexes does not require editing DSL tests.

---

## Task 2: Colour bands

One task because the band constants couple `led_slots.py`, `led_status.py`, and both test files. Deleting `SLOT_AMBER` breaks `tests/test_sidepulse.py` at import, so splitting this would leave a commit with a red suite.

**Files:**
- Modify: `src/sidepulse/led_slots.py`
- Modify: `src/sidepulse/led_status.py:193-261`
- Test: `tests/test_led_slots.py`
- Test: `tests/test_sidepulse.py`

- [ ] **Step 1: Rewrite the band tests in `tests/test_led_slots.py`**

Replace the import block at lines 20-29:

```python
from sidepulse.led_slots import (
    BAND_ALERT,
    BAND_DONE,
    BAND_WORKING,
    FLASH_SECONDS,
    SLOT_EMPTY,
    SlotAllocator,
    slot_band,
    slot_rank,
)
```

Replace the whole `SlotRankTests` class (lines 66-88):

```python
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
        """A None peak is what replaced FLASHING_RANKS."""
        self.assertIsNotNone(BAND_ALERT.flash_peak)
        self.assertIsNotNone(BAND_DONE.flash_peak)
        self.assertIsNone(BAND_WORKING.flash_peak)

    def test_every_flashing_band_peaks_brighter_than_it_settles(self) -> None:
        """The flash pulses up from the baseline, so a dimmer peak would invert it.

        The hex values are provisional and expected to be re-tuned on hardware.
        This is the invariant that must survive the tuning.
        """
        for band in (BAND_ALERT, BAND_DONE):
            with self.subTest(band=band):
                steady = sum(int(band.steady[index:index + 2], 16) for index in (1, 3, 5))
                peak = sum(int(band.flash_peak[index:index + 2], 16) for index in (1, 3, 5))
                self.assertLess(steady, peak)
```

- [ ] **Step 2: Update the remaining colour references in `tests/test_led_slots.py`**

These are mechanical substitutions of the old constants, which no longer exist:

- `SLOT_AMBER` → `BAND_ALERT.steady`
- `SLOT_CYAN` → `BAND_WORKING.steady`
- `SLOT_GREEN` → `BAND_DONE.steady`

Affected lines: 98, 99, 112, 122, 145, 153, 180, 196, 254, 265, 302, 313, 331, 390-391, 431. `SLOT_EMPTY` is unchanged everywhere it appears.

Two of those need more than a substitution, because `[SLOT_AMBER] * 8` style lists now read oddly against a band:

```python
# line 196, inside test_a_less_important_newcomer_gets_no_slot
        self.assertEqual([slot.color for slot in slots], [BAND_ALERT.steady] * 8)

# line 331, inside test_a_two_led_strip_allocates_two_slots
        self.assertEqual(
            sorted(slot.color for slot in slots),
            sorted([BAND_ALERT.steady, BAND_WORKING.steady]),
        )
```

- [ ] **Step 3: Rewrite `FlashClockTests` in `tests/test_led_slots.py`**

Replace the whole class (lines 236-314). Three tests change meaning and two are new:

```python
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
```

- [ ] **Step 4: Run the allocator tests to verify they fail**

```sh
.venv/bin/python -m pytest tests/test_led_slots.py -q
```

Expected: collection error, `ImportError: cannot import name 'BAND_ALERT' from 'sidepulse.led_slots'`.

- [ ] **Step 5: Replace the colour table in `src/sidepulse/led_slots.py`**

Change the import at line 22 to pull in `field`:

```python
from dataclasses import dataclass, field
```

Replace lines 29-68 — the four colour constants, `FLASH_SECONDS`, `MODE_SLOT_RANK`, `MODE_SLOT_COLOR`, and `FLASHING_RANKS` — with this. `MODE_SLOT_RANK` is carried over unchanged; it is reproduced here so you are not diffing against memory:

```python
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
# Provisional. Numeric intensity is not perceived intensity -- one tier fraction
# does not read as one tier across red, blue and green -- so expect to retune all
# five against real hardware. Nothing outside this table needs to change when you
# do.
BAND_ALERT = SlotBand(steady="#3D0000", flash_peak="#FF0000")  # low 24% / high 100%
BAND_WORKING = SlotBand(steady="#002A80", flash_peak=None)  # medium 50%
BAND_DONE = SlotBand(steady="#003D18", flash_peak="#008033")  # low 24% / medium 50%


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
```

Then replace `slot_color` (lines 76-78) with:

```python
def slot_band(mode: AgentMode) -> SlotBand | None:
    """Colour band, or None for a mode that holds no slot."""
    return MODE_SLOT_BAND.get(mode)
```

`slot_rank` at lines 71-73 stays exactly as it is.

- [ ] **Step 6: Give `Slot` the peak, keyword-only**

Replace the `Slot` and `_Occupant` dataclasses (lines 81-95):

```python
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
```

- [ ] **Step 7: Point the allocator internals at bands**

Three edits inside `SlotAllocator`.

In `update`, the hold loop (lines 142-149) compares bands instead of colour strings:

```python
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
```

The assign loop (lines 153-164) renames its unpacked variable:

```python
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
```

`_displayable` (lines 168-181) returns bands:

```python
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
```

And `_render` (lines 208-225) delegates to the band, which is where the flash rule now lives:

```python
    def _render(self, now: datetime) -> tuple[Slot, ...]:
        slots: list[Slot] = []
        for occupant in self._slots:
            if occupant is None:
                slots.append(EMPTY_SLOT)
                continue
            remaining = 0.0
            if occupant.band.flash_peak is not None:
                age = max(0.0, (now - occupant.entered_at).total_seconds())
                remaining = max(0.0, self.flash_seconds - age)
            slots.append(occupant.band.slot(remaining))
        return tuple(slots)
```

Finally, update the module docstring's second paragraph (lines 5-7) to describe bands rather than colours:

```python
"""Slot allocation for the multiagent LED display.

One LED is one top-level agent session. A session takes a slot when it becomes
displayable, keeps it while it stays displayable, and gives it up when it drops
out of the set it was fed. A colour band says what the session needs; bands that
carry a flash peak blink for the first five minutes after a session enters them
and then settle to their steady colour, so "changed recently" reads differently
from "has been like this a while".
```

- [ ] **Step 8: Run the allocator tests to verify they pass**

```sh
.venv/bin/python -m pytest tests/test_led_slots.py -q
```

Expected: PASS, 34 tests. `tests/test_sidepulse.py` is still red at import — Step 9 fixes that.

- [ ] **Step 9: Rewrite the renderer tests in `tests/test_sidepulse.py`**

Change the `led_slots` import block (lines 69-77):

```python
from sidepulse.led_slots import (
    BAND_ALERT,
    BAND_DONE,
    BAND_WORKING,
    FLASH_SECONDS,
    SLOT_EMPTY,
    Slot,
    SlotAllocator,
)
```

Replace the multiagent rendering tests. The mixed-vector test grows a slot so it can show a working slot being absent from the flash line:

```python
    def test_multiagent_program_renders_a_baseline_and_a_flash_line(self) -> None:
        """Line 1 is every slot's settled colour; line 2 pulses the recent ones up."""
        slots = (
            BAND_ALERT.slot(FLASH_SECONDS),
            BAND_ALERT.slot(),
            BAND_WORKING.slot(FLASH_SECONDS),
            BAND_DONE.slot(FLASH_SECONDS),
            BAND_DONE.slot(),
            Slot(SLOT_EMPTY),
            Slot(SLOT_EMPTY),
            Slot(SLOT_EMPTY),
        )

        program = program_for_slots(slots, brightness=128)

        validate_led_text(program)
        self.assertEqual(
            program.splitlines(),
            [
                "brightness 128",
                "0:#3D0000 1:#3D0000 2:#002A80 3:#003D18 "
                "4:#003D18 5:#000000 6:#000000 7:#000000",
                "0:#FF0000 900ms pulse; 3:#008033 900ms pulse",
                "repeat 328",
            ],
        )
```

Slot 2 asks for a full flash window and still does not appear on line 2, because `BAND_WORKING.flash_peak` is None. That is the assertion, not an oversight.

```python
    def test_multiagent_program_omits_the_flash_line_when_nothing_is_recent(self) -> None:
        slots = (BAND_ALERT.slot(),) + (Slot(SLOT_EMPTY),) * 7

        program = program_for_slots(slots, brightness=128)

        validate_led_text(program)
        self.assertEqual(
            program.splitlines(),
            [
                "brightness 128",
                "0:#3D0000 1:#000000 2:#000000 3:#000000 "
                "4:#000000 5:#000000 6:#000000 7:#000000",
            ],
        )
        self.assertNotIn("repeat", program)
        self.assertNotIn("pulse", program)

    def test_multiagent_program_omits_the_flash_line_for_an_all_working_strip(self) -> None:
        """No band on the strip flashes, so the program is one static line."""
        program = program_for_slots((BAND_WORKING.slot(FLASH_SECONDS),) * 8)

        validate_led_text(program)
        self.assertEqual(len(program.splitlines()), 1)
        self.assertNotIn("pulse", program)
        self.assertNotIn("repeat", program)

    def test_multiagent_program_never_uses_the_off_keyword_for_a_slot(self) -> None:
        """``0:off`` is rejected by the parser with bad-index; ``0:#000000`` is not."""
        program = program_for_slots((Slot(SLOT_EMPTY),) * 8)

        validate_led_text(program)
        self.assertNotIn("off", program)
        self.assertIn("0:#000000", program)

    def test_multiagent_repeat_count_covers_the_longest_remaining_flash(self) -> None:
        def repeat_line(remaining: float) -> str:
            slots = (BAND_ALERT.slot(remaining),) + (Slot(SLOT_EMPTY),) * 7
            return program_for_slots(slots).splitlines()[-1]

        # ceil(remaining_ms / 917ms), the measured blink cycle for a 900ms pulse.
        self.assertEqual(repeat_line(FLASH_SECONDS), "repeat 328")
        self.assertEqual(repeat_line(1.0), "repeat 2")
        self.assertEqual(repeat_line(0.917), "repeat 1")
        # repeat 0 is rejected by the parser, so the floor is 1, not 0.
        self.assertEqual(repeat_line(0.0001), "repeat 1")
        # The parser caps repeat at 65535.
        self.assertEqual(repeat_line(10_000_000.0), "repeat 65535")

    def test_multiagent_repeat_count_follows_the_longest_flashing_slot(self) -> None:
        slots = (
            BAND_DONE.slot(10.0),
            BAND_ALERT.slot(200.0),
            BAND_DONE.slot(30.0),
        ) + (Slot(SLOT_EMPTY),) * 5

        program = program_for_slots(slots)

        self.assertEqual(program.splitlines()[-1], "repeat 219")

    def test_multiagent_worst_case_program_fits_the_device_limits(self) -> None:
        """Eight occupied slots all flashing is the most bytes and lines it can emit."""
        slots = (BAND_ALERT.slot(FLASH_SECONDS),) * 8

        program = program_for_slots(slots, brightness=128)

        validate_led_text(program)
        self.assertLessEqual(len(program.encode("utf-8")), MAX_LED_BYTES)
        self.assertLessEqual(len(program.splitlines()), MAX_LED_LINES)
        self.assertEqual(len(program.splitlines()), 4)
        self.assertEqual(len(program.encode("utf-8")), 288)

    def test_multiagent_program_defaults_to_full_brightness(self) -> None:
        """Intensity is in the colours now, so the default must not scale them down.

        At 128 -- the old default -- "high intensity red" would have rendered at
        half, halving the whole scheme.
        """
        program = program_for_slots((BAND_DONE.slot(),) + (Slot(SLOT_EMPTY),) * 7)

        self.assertNotIn("brightness", program)

    def test_multiagent_program_emits_the_directive_when_the_slider_is_down(self) -> None:
        """The slider is a master gain over the bands, not a replacement for them."""
        slots = (BAND_DONE.slot(),) + (Slot(SLOT_EMPTY),) * 7

        program = program_for_slots(slots, brightness=64)

        self.assertEqual(program.splitlines()[0], "brightness 64")

    def test_multiagent_program_renders_a_two_led_strip(self) -> None:
        slots = (BAND_ALERT.slot(FLASH_SECONDS), BAND_WORKING.slot())

        program = program_for_slots(slots, brightness=128)

        validate_led_text(program)
        self.assertEqual(
            program.splitlines(),
            [
                "brightness 128",
                "0:#3D0000 1:#002A80",
                "0:#FF0000 900ms pulse",
                "repeat 328",
            ],
        )

    def test_the_settle_write_leaves_the_baseline_byte_identical(self) -> None:
        """Crossing five minutes only removes a line; it never restates line 1.

        This is why `repeat` expiry is now harmless: the device is already holding
        the settled appearance by the time the host notices the boundary, so a
        dead app leaves the strip correct rather than frozen bright.
        """
        flashing = (BAND_ALERT.slot(FLASH_SECONDS),) + (Slot(SLOT_EMPTY),) * 7
        settled = (BAND_ALERT.slot(),) + (Slot(SLOT_EMPTY),) * 7

        before = program_for_slots(flashing, brightness=128).splitlines()
        after = program_for_slots(settled, brightness=128).splitlines()

        self.assertEqual(len(before), 4)
        self.assertEqual(len(after), 2)
        self.assertEqual(before[:2], after[:2])

    def test_multiagent_render_key_ignores_the_repeat_count(self) -> None:
        """The dedupe key must not move as the flash window ticks down.

        Remaining flash time changes on every refresh. If it reached the key,
        the 15s timer alone would rewrite LEDS.LED forever.
        """
        early = (BAND_ALERT.slot(FLASH_SECONDS),) + (Slot(SLOT_EMPTY),) * 7
        late = (BAND_ALERT.slot(12.0),) + (Slot(SLOT_EMPTY),) * 7
        settled = (BAND_ALERT.slot(),) + (Slot(SLOT_EMPTY),) * 7

        self.assertEqual(slot_render_key(early, 128), slot_render_key(late, 128))
        self.assertNotEqual(slot_render_key(late, 128), slot_render_key(settled, 128))
        self.assertNotEqual(slot_render_key(early, 128), slot_render_key(early, 255))
```

Add one controller-level test for the boundary, next to the other `MultiAgentLedController` tests. The renderer test above proves the program shape; this proves the write budget:

```python
    def test_multiagent_controller_writes_once_when_a_slot_settles(self) -> None:
        """Ticking the flash window down is not a write; crossing five minutes is one.

        And the write it does emit restates the baseline unchanged -- the device is
        already holding the settled appearance by then, because `repeat` expired
        onto it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "SidePulsePro"
            device.mkdir()
            controller = MultiAgentLedController(
                device_path=device, rng=random.Random(1234)
            )
            base = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
            statuses = (
                AgentStatus(
                    provider="claude",
                    agent_id="claude:session:a",
                    display_name="a",
                    mode=AgentMode.WAITING_FOR_INPUT,
                    updated_at=base,
                    event_name="Notification",
                ),
            )

            flashing = controller.sync_statuses(statuses, now=base)
            midway = controller.sync_statuses(
                statuses, now=base + timedelta(seconds=FLASH_SECONDS / 2)
            )
            settled = controller.sync_statuses(
                statuses, now=base + timedelta(seconds=FLASH_SECONDS + 1)
            )

            self.assertTrue(flashing.changed)
            self.assertFalse(midway.changed)
            self.assertTrue(settled.changed)

            # Default brightness is 255, so no directive: baseline, flash, repeat.
            self.assertEqual(len(flashing.program.splitlines()), 3)
            self.assertEqual(len(settled.program.splitlines()), 1)
            self.assertEqual(
                flashing.program.splitlines()[0], settled.program.splitlines()[0]
            )
```

Then the firmware-engine test. Replace its `vectors` list and add the pulse-up assertions Task 1 measured by hand:

```python
    def test_multiagent_programs_parse_in_the_firmware_engine(self) -> None:
        """The size-only validator cannot catch a DSL mistake; the real parser can."""
        try:
            from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
        except ImportError as exc:
            self.skipTest(str(exc))

        try:
            controller = SdLedWasmController(led_count=8)
        except LedWasmUnavailableError as exc:
            self.skipTest(str(exc))

        vectors = [
            (Slot(SLOT_EMPTY),) * 8,
            (BAND_ALERT.slot(FLASH_SECONDS),) * 8,
            (BAND_WORKING.slot(),) * 8,
            (BAND_DONE.slot(),) * 8,
            (
                BAND_ALERT.slot(FLASH_SECONDS),
                BAND_ALERT.slot(),
                BAND_WORKING.slot(),
                BAND_DONE.slot(120.0),
                BAND_DONE.slot(),
                Slot(SLOT_EMPTY),
                Slot(SLOT_EMPTY),
                Slot(SLOT_EMPTY),
            ),
        ]
        for slots in vectors:
            program = program_for_slots(slots)
            with self.subTest(program=program):
                result = controller.parse(program, 0)
                self.assertTrue(result.ok, f"{result.error_name} at line {result.line}")

    def test_multiagent_flash_pulses_up_from_the_settled_colour(self) -> None:
        """The blink must never go below the baseline, and must end on it.

        Measured against sdled.wasm: a flashing alert slot rises from 61 to 254
        and returns to 61, while a working slot absent from the flash line holds
        every sample. The old pulse-to-black shape would put the minimum at 0 and
        leave the post-repeat hold on the bright colour.
        """
        try:
            from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
        except ImportError as exc:
            self.skipTest(str(exc))

        try:
            engine = SdLedWasmController(led_count=8)
        except LedWasmUnavailableError as exc:
            self.skipTest(str(exc))

        # remaining=1.0s gives `repeat 2`, a ~1834ms window, so the settled
        # sample at 3000ms is safely past it without stepping 300 seconds.
        slots = (BAND_ALERT.slot(1.0), BAND_WORKING.slot()) + (Slot(SLOT_EMPTY),) * 6
        program = program_for_slots(slots)
        self.assertEqual(program.splitlines()[-1], "repeat 2")

        engine.reset(0)
        self.assertTrue(engine.parse(program, 0).ok)

        samples = [engine.step(moment) for moment in range(0, 1000, 50)]
        alert_reds = [pixels[0][0] for pixels in samples]

        steady_red = int(BAND_ALERT.steady[1:3], 16)
        peak_red = int(BAND_ALERT.flash_peak[1:3], 16)
        self.assertEqual(min(alert_reds), steady_red)
        self.assertGreaterEqual(max(alert_reds), peak_red - 5)

        working_rgb = tuple(
            int(BAND_WORKING.steady[index:index + 2], 16) for index in (1, 3, 5)
        )
        for pixels in samples:
            self.assertEqual(pixels[1], working_rgb)

        settled = engine.step(3000)
        self.assertEqual(settled[0], (steady_red, 0, 0))
        self.assertEqual(settled[1], working_rgb)
```

Finally, the `sync_leds_now` test at line 1884 constructs a `Slot` positionally. Change that one line:

```python
                        slots=(BAND_ALERT.slot(FLASH_SECONDS),),
```

and the fake program string on the line above it, which is only cosmetic but should not name a dead colour:

```python
                        program="brightness 128\n0:#3D0000",
```

- [ ] **Step 10: Run the renderer tests to verify they fail**

```sh
.venv/bin/python -m pytest tests/test_sidepulse.py -q -k multiagent
```

Expected: failures on program text — the flash line still emits `0:#000000 900ms pulse`, and `test_multiagent_program_defaults_to_full_brightness` still sees `brightness 128`.

- [ ] **Step 11: Point the renderer at the peak**

In `src/sidepulse/led_status.py`, change `SLOT_BRIGHTNESS` (line 193) and its comment block. Replace lines 184-193:

```python
# --- multiagent slot rendering -------------------------------------------------
#
# The strip becomes N independent slots instead of one aggregate indicator. Line
# 1 is every slot's *settled* colour; line 2 pulses only the recently-changed
# slots up to their band's peak. `pulse` returns to the line's start colour, so
# each cycle is a blink up from the baseline, and slots the flash line does not
# mention hold rock steady. After the repeat count is exhausted the device holds
# its final state -- which is the baseline, i.e. exactly the post-five-minute
# appearance. That is why a dead app leaves the strip correct rather than frozen
# on a state that has expired.

# Intensity lives in the band colours (see led_slots), so this directive is free
# to carry the user's brightness slider as a master gain. 255 emits no directive
# at all; anything lower scales every band together.
SLOT_BRIGHTNESS = 255
```

Then the flash line in `program_for_slots` (lines 237-244):

```python
    flashing = [index for index, slot in enumerate(slots) if slot.flashing]
    if flashing:
        lines.append(
            "; ".join(
                f"{index}:{slots[index].flash_color} {SLOT_FLASH_PULSE_MS}ms pulse"
                for index in flashing
            )
        )
        lines.append(f"repeat {slot_repeat_count(slots)}")
```

And the dedupe key (lines 259-261):

```python
    return (normalize_brightness(brightness),) + tuple(
        (slot.color, slot.flash_color) for slot in slots
    )
```

`SLOT_EMPTY` is still imported at line 18 and still used for the empty-slot baseline, so leave that import alone.

- [ ] **Step 12: Run the full suite**

```sh
SIDEPULSE_SKIP_CLEAN_INSTALL=1 .venv/bin/python -m pytest tests -q
```

Expected: PASS. If `tests/test_status_bar_ui.py` skips, that is normal without AppKit; force it with `SIDEPULSE_REQUIRE_UI_TESTS=1` if you want it to run.

- [ ] **Step 13: Commit**

```bash
git add src/sidepulse/led_slots.py src/sidepulse/led_status.py \
        tests/test_led_slots.py tests/test_sidepulse.py
git commit -m "Give each slot colour an intensity, and pulse up instead of down

Three flat colours become three bands, each carrying its own intensity: red
flashes bright and settles dim, blue holds medium and never flashes, green
flashes medium and settles dim. Intensity has to live in the colour values
because the brightness directive is program-global and cannot vary per slot.

The flash inverts as a consequence, and that is the part worth having. Line 1
was each slot's colour and line 2 pulsed it down to black, so when repeat expired
the device held the pre-settle appearance and the strip was wrong until the host
rewrote. Line 1 is now the settled colour and line 2 pulses up to the peak, which
puts repeat expiry exactly on the post-five-minute look. Measured against
sdled.wasm: a flashing alert slot runs 61 -> 254 -> 61 and holds 61 past the
window, and a working slot absent from line 2 does not move at all.

FLASHING_RANKS is deleted rather than edited. Whether a slot flashes was always a
property of its colour, not its rank, and the working band having no peak makes
that load-bearing. Ranks stay for eviction alone, so the allocator body and every
eviction test carry over untouched.

Slot.flash_color is keyword-only because argument two used to be a bool: a stale
positional True stays truthy, so .flashing would read correctly while the
renderer emitted \`0:True 900ms pulse\`.

SLOT_BRIGHTNESS moves 128 -> 255. With intensity in the colours, leaving it at
128 would render high-intensity red at half and halve the whole scheme."
```

---

## Task 3: Wire the brightness slider through

`multiagent_controller_for_device` refuses `device.brightness` on the grounds that eight LEDs at 255 glare. That reason is gone: the bands are already dim where dimness was wanted, so the directive is free to carry the slider.

**Files:**
- Modify: `src/sidepulse/status_bar.py:1769-1781`
- Modify: `src/sidepulse/status_bar.py:2499-2510`
- Test: `tests/test_sidepulse.py:1616-1671`

- [ ] **Step 1: Write the failing tests**

Replace `test_status_bar_multiagent_submenu_omits_the_inert_brightness_controls` (lines 1616-1671) with two tests. The first mirrors the assertions in `test_status_bar_aggregate_submenu_keeps_the_brightness_controls` immediately below it:

```python
    def test_status_bar_multiagent_submenu_keeps_the_brightness_controls(self) -> None:
        """The slider is live in this mode now that intensity is in the colours.

        It used to be hidden because the strip hardcoded brightness 128 and
        ignored the setting, so the readout would have claimed a number the device
        did not use. The bands carry intensity themselves, so the directive is
        free to be the master gain the slider always looked like.
        """
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        device = status_bar.StatusBarDevice(
            device_id="/Volumes/SidePulsePro",
            name="SidePulse Pro",
            root=Path("/Volumes/SidePulsePro"),
            target=Path("/Volumes/SidePulsePro/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_MULTIAGENT,
            brightness=255,
        )

        submenu = status_bar.build_device_menu_item(
            device, SimpleNamespace()
        ).submenu()
        titles = [
            submenu.itemAtIndex_(index).title()
            for index in range(submenu.numberOfItems())
        ]
        slider_count = sum(
            1
            for index in range(submenu.numberOfItems())
            if submenu.itemAtIndex_(index).view() is not None
        )

        self.assertIn("Brightness 100%", titles)
        self.assertEqual(slider_count, 1)
        # The mode picker itself must survive, or there is no way back out.
        self.assertIn("One LED Per Agent", titles)
        self.assertIn("Agent Status", titles)

    def test_status_bar_multiagent_controller_takes_the_device_brightness(self) -> None:
        """A slider drag has to reach the strip, not just the menu."""
        try:
            from sidepulse import status_bar
        except SystemExit as exc:
            self.skipTest(str(exc))

        device = status_bar.StatusBarDevice(
            device_id="/Volumes/SidePulsePro",
            name="SidePulse Pro",
            root=Path("/Volumes/SidePulsePro"),
            target=Path("/Volumes/SidePulsePro/LEDS.LED"),
            connected=True,
            display=LED_DISPLAY_MULTIAGENT,
            brightness=96,
        )
        fake = SimpleNamespace(multiagent_led_controllers_by_device={})

        first = status_bar.StatusBarController.multiagent_controller_for_device(
            fake, device
        )
        dimmed = status_bar.StatusBarController.multiagent_controller_for_device(
            fake, replace(device, brightness=32)
        )

        self.assertEqual(first.brightness, 96)
        # Same cached controller, moved by the second call.
        self.assertIs(dimmed, first)
        self.assertEqual(dimmed.brightness, 32)
```

`replace` comes from `dataclasses`, and `tests/test_sidepulse.py` does not import it yet. Add this line after `import unittest` (line 13) and before `from datetime import ...` (line 14):

```python
from dataclasses import replace
```

`StatusBarDevice` is a frozen dataclass (`status_bar.py:205`), so `replace` is the way to vary one field.

- [ ] **Step 2: Run the tests to verify they fail**

```sh
.venv/bin/python -m pytest tests/test_sidepulse.py -q -k "multiagent_submenu or multiagent_controller_takes"
```

Expected: `test_status_bar_multiagent_submenu_keeps_the_brightness_controls` fails with `'Brightness 100%' not found in [...]`, and `test_status_bar_multiagent_controller_takes_the_device_brightness` fails with `96 != 255` — the controller default, because nothing assigns it.

- [ ] **Step 3: Assign the brightness in `status_bar.py`**

Replace the comment and add the assignment (lines 1777-1781):

```python
        controller.device_path = device.target
        controller.brightness = device.brightness
        return controller
```

This now matches `agent_controller_for_device` and `battery_controller_for_device` exactly, which is the point — there is no longer anything special about this one.

- [ ] **Step 4: Un-hide the submenu controls**

Replace the comment and guard (lines 2499-2510):

```python
    if device.device_id != VIRTUAL_DEVICE_ID:
        submenu.addItem_(NSMenuItem.separatorItem())
        submenu.addItem_(disabled_menu_item(f"Brightness {brightness_percent(device.brightness)}%"))
        submenu.addItem_(build_brightness_slider_item(device, target))
```

Check whether `LED_DISPLAY_MULTIAGENT` is still referenced elsewhere in `status_bar.py` before removing any import:

```sh
grep -c LED_DISPLAY_MULTIAGENT src/sidepulse/status_bar.py
```

Expected: several hits (the mode picker and `active_led_display_kind_for_device` both use it), so the import stays.

- [ ] **Step 5: Run the tests to verify they pass**

```sh
SIDEPULSE_REQUIRE_UI_TESTS=1 .venv/bin/python -m pytest tests/test_sidepulse.py -q -k "multiagent or brightness"
```

Expected: PASS. `SIDEPULSE_REQUIRE_UI_TESTS=1` makes an AppKit-less environment fail loudly instead of skipping the tests that matter here.

- [ ] **Step 6: Run the full suite**

```sh
SIDEPULSE_SKIP_CLEAN_INSTALL=1 .venv/bin/python -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sidepulse/status_bar.py tests/test_sidepulse.py
git commit -m "Let the brightness slider reach the multiagent strip

multiagent_controller_for_device refused device.brightness because the strip
hardcoded 128, on the grounds that eight LEDs at 255 glare. The bands are dim
where dimness was wanted now, so the directive is free to be the master gain the
slider always looked like -- final output is hue x band tier x slider.

The submenu items come back with it. Hiding them was the honest choice while the
setting was ignored; showing a readout the strip agrees with is better."
```

---

## Task 4: Documentation

`docs/usage.md` describes the old scheme in four places. All four are now wrong, and one of them promises the brightness gotcha that Task 3 just removed.

**Files:**
- Modify: `docs/usage.md`
- Modify: `docs/superpowers/specs/2026-08-24-multiagent-led-slots-design.md`

- [ ] **Step 1: Replace the colour table and flash description**

Replace `docs/usage.md` lines 11-30 (from `## What you see` through `Brightness is 128.`):

```markdown
## What you see

| Band | Settled | Flashes up to | Means | Modes |
|---|---|---|---|---|
| Red | `#3D0000` | `#FF0000` | that session wants something from you | `BLOCKED_ERROR`, `WAITING_FOR_INPUT` |
| Blue | `#002A80` | — never flashes | working | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` |
| Green | `#003D18` | `#008033` | finished, output unread | `COMPLETED`, `IDLE_READY` |
| Off | `#000000` | — | empty slot | — |

Red and green **blink for the first five minutes** after a session enters the
band, pulsing up from the settled colour and back, then hold settled. Settled is
not "stale" — it is "this has been true for a while".

Five minutes is the prompt cache TTL, and that is the whole point of the
threshold rather than a round number. A blinking slot is one whose cache is
probably still warm, so picking it up now is cheap. Once it settles, resuming
pays full price — which is also why the flash deliberately stops instead of
nagging until answered. A slot that blinked forever would be asserting something
no longer true.

Blue does not blink and does not settle, because an actively running agent has no
cache-warmth story: it is running, and it will still be running in six minutes.
The cost is that a session working for an hour looks identical to one that started
two seconds ago. Working is the boring state.

Intensity lives in those hex values, not in the `brightness` directive, which is
program-global and so cannot vary per slot. The directive carries your brightness
slider on top, as a master gain over all three bands.

The hex values are provisional. Numeric intensity is not perceived intensity, and
one tier fraction does not read as one tier across red, blue and green — expect
them to be retuned against hardware. They are all in one table in
`led_slots.py`, so tuning them touches nothing else.
```

- [ ] **Step 2: Replace the brightness gotcha**

Replace lines 139-146 (the `### Per-device brightness is ignored in this mode` section) with:

```markdown
### Turning the slider down can hide the settled colours

Per-device brightness works in this mode: the slider emits `brightness N`, which
scales all three bands together. The settled colours are already the dim end of
each band, so at a low slider setting they can quantize to nearly nothing — at
25%, `#3D0000` is an effective red of about 15/255.

That is accepted rather than floored. Dimming the strip to almost nothing is a
reasonable thing to want from a brightness slider, and a floor would surprise
whoever asked for it. If settled slots are too faint for you, raise the slider or
raise the `steady` values in `led_slots.py`.
```

- [ ] **Step 3: Fix the sub-agent colour reference**

Line 166 says "turns the parent's slot amber". Change that sentence's ending:

```markdown
child stopped on a permission prompt turns the parent's slot red.
```

- [ ] **Step 4: Replace the sample program and its explanation**

Replace lines 251-270 (from the `A live strip with four sessions` paragraph through the `repeat` explanation). The replacement contains a fenced `text` block, so it is shown here inside `~~~` fences to keep the nesting readable — write only the inner content into `usage.md`:

~~~markdown
A live strip with four sessions — three waiting on input, one working, all four
changed within the last five minutes:

```text
brightness 128
0:#3D0000 1:#000000 2:#000000 3:#002A80 4:#3D0000 5:#000000 6:#000000 7:#3D0000
0:#FF0000 900ms pulse; 4:#FF0000 900ms pulse; 7:#FF0000 900ms pulse
repeat 328
```

Line 2 is every slot's settled colour. Line 3 pulses only the recently-changed
slots up to their band's peak and back; `pulse` returns to the line's start
colour, so each cycle is a blink up from the baseline and unmentioned LEDs hold
rock steady. Slot 3 is working and so never appears on line 3 at all.

`repeat` is `ceil(longest_remaining_flash / 917ms)` — 917 ms being the measured
blink cycle for a `900ms pulse`, not a value derived from the pulse duration. It
counts down as the flash window closes, which is why the write-dedupe key
excludes it; keying on the program text would rewrite `LEDS.LED` every 15 s
forever.

Because the count is finite *and* line 2 is the baseline, a dead app leaves a
device holding the settled appearance — not one blinking indefinitely, and not
one frozen on the bright pre-settle colour either. The five-minute write is
therefore visually a no-op: by the time the host notices the boundary the device
already looks right, and the write only removes a slot from line 3.
~~~

- [ ] **Step 5: Point the old design doc at the new one**

Add this immediately after the `Status:` line at the top of
`docs/superpowers/specs/2026-08-24-multiagent-led-slots-design.md`:

```markdown
> **Superseded in part.** The state table, program shape and brightness handling
> here were replaced by
> [`2026-08-25-multiagent-led-colour-bands-design.md`](2026-08-25-multiagent-led-colour-bands-design.md).
> The strip now carries three colour bands with their own intensity, the flash
> pulses up from the settled colour rather than down to black, and the brightness
> slider is live. Everything else in this document — slot allocation, eviction,
> the status feed, sleep behaviour, the write budget — still holds.
```

- [ ] **Step 6: Check every hex in the docs matches the code**

```sh
grep -o '#[0-9A-Fa-f]\{6\}' docs/usage.md | sort -u
grep -o '#[0-9A-Fa-f]\{6\}' src/sidepulse/led_slots.py | sort -u
```

Expected: both print exactly `#002A80`, `#003D18`, `#008033`, `#000000`, `#3D0000`, `#FF0000`. No `#FF3A00`, `#00E5FF`, or `#00FF66` may survive in `docs/usage.md`.

- [ ] **Step 7: Commit**

```bash
git add docs/usage.md docs/superpowers/specs/2026-08-24-multiagent-led-slots-design.md
git commit -m "Document the colour bands and the live brightness slider

docs/usage.md described amber, cyan and green, a strip pinned at brightness 128
and a flash that pulsed down to black. All three are now wrong, and the section
promising that per-device brightness was a follow-up describes something that
shipped.

The 2026-08-24 design doc gets a superseded-in-part pointer rather than an edit.
Its state table and program shape are wrong now; its slot allocation, eviction,
status feed and sleep sections all still hold, so sending readers away entirely
would lose more than it saves."
```

---

## Task 5: Verify on the device

The suite proves the program text. It cannot prove that red reads as urgent and blue as calm at these numeric values — that is what this task is for, and it is the reason the hexes are called provisional.

**Files:**
- No files change unless the colours read badly, in which case only the five constants in `src/sidepulse/led_slots.py` move.

- [ ] **Step 1: Print the program without touching the device**

```sh
.venv/bin/python -m sidepulse agent-monitor leds --once --dry-run --display multiagent
```

Expected: a program whose baseline line uses only the six hexes from Task 4 Step 6, and no `brightness` line at all — the CLI relies on the controller default, which is now 255.

- [ ] **Step 2: Drive a real strip, if one is mounted**

```sh
ls /Volumes | grep -i sidepulse && .venv/bin/python -m sidepulse agent-monitor leds --display multiagent
```

Leave it running and start an agent session in another terminal. Watch for:

- A waiting session blinks red and never goes dark between blinks.
- A working session holds one steady blue and does not blink.
- After five minutes, a red slot stops blinking and holds dim red — with no visible jump at the moment it settles.
- Killing the process leaves the strip on the settled colours, not frozen bright.

- [ ] **Step 3: Confirm the slider moves the strip**

Restart the menu-bar app so it picks up the new code, then drag the brightness slider in the device submenu:

```sh
launchctl kickstart -k gui/$(id -u)/io.sidepulse.agentstatus
```

Expected: the readout and slider are present in multiagent mode, and dragging visibly changes the strip. Do **not** use `sidepulse status-bar stop && start` as a restart — `stop` sets `uninstall = True` and removes the LaunchAgent plist.

- [ ] **Step 4: Retune only if needed, then commit**

If the bands read badly — most likely blue looking far weaker than red, or the settled colours being too faint to see — change only `BAND_ALERT`, `BAND_WORKING`, and `BAND_DONE` in `src/sidepulse/led_slots.py`, then update the hexes in `docs/usage.md` and the two exact-program-text tests in `tests/test_sidepulse.py`.

`test_every_flashing_band_peaks_brighter_than_it_settles` guards the one invariant tuning must not break. Re-run the suite before committing:

```sh
SIDEPULSE_SKIP_CLEAN_INSTALL=1 .venv/bin/python -m pytest tests -q
```

If nothing needed tuning, there is nothing to commit and this task ends here.

---

## Not in this plan

- **Perceptual calibration as a spec'd deliverable.** The values are provisional and Task 5 is a look-at-it step, not a measurement protocol.
- **Multiagent on the Dot.** Two LEDs is not enough for the mode; the Dot stays on aggregate `agent`.
- **A brightness floor for the low tier.** Explicitly accepted in the spec: dimming the strip to nearly nothing is a legitimate use of a brightness slider.
- **Changing which sessions get a slot.** `MODE_SLOT_RANK`, the eviction rules, and `collector.multiagent_statuses` are untouched, and their tests carrying over unchanged is the check that this stayed a presentation change.
