# Multiagent LED colour bands

Date: 2026-08-25
Status: accepted, implementing
Supersedes the state table, program shape and brightness handling in
[`2026-08-24-multiagent-led-slots-design.md`](2026-08-24-multiagent-led-slots-design.md).

## Summary

Replace the multiagent strip's three flat colours with three colour *bands*, each
carrying its own intensity. Red means a session wants you and flashes bright
until it settles dim. Blue means working and never flashes. Green means finished
and flashes until it settles dim. Intensity moves into the colour values, which
frees the global `brightness` directive to become what the device submenu's
slider always should have driven.

This is a presentation change. `AgentMode`, `LedDisplayState`,
`collector.multiagent_statuses`, and the allocator's assign/hold/release/evict
logic are untouched.

## Goals

- Distinguish "wants you" from "working" from "finished" by hue, and
  "changed recently" from "has been like this a while" by intensity plus flash.
- Make the brightness slider live in multiagent mode, which
  `multiagent_controller_for_device` (`status_bar.py:1776-1779`) currently refuses on
  the grounds that it is "not wired up yet".
- Keep the property that a dead status-bar app leaves the strip *correct*, not
  merely still.
- Add no new timer and no new polling.

## Non-goals

- Changing which sessions get a slot, or which one evicts which. `MODE_SLOT_RANK`
  and the allocator body are unchanged.
- Enabling multiagent on the Dot. Two LEDs is not enough for the mode.
- Perceptual calibration. The hex values below are starting points, tuned on
  hardware after the code lands.

## Colour bands

Whether a slot flashes stops being a property of its rank and becomes a property
of its band. That is what it always was semantically, and it is now load-bearing:
blue is the band with no peak, so `FLASHING_RANKS` has nothing left to decide and
is deleted.

```python
@dataclass(frozen=True)
class SlotBand:
    steady: str             # held when settled; also the flash baseline
    flash_peak: str | None  # pulse target; None means this band never flashes


BAND_ALERT = SlotBand(steady="#3D0000", flash_peak="#FF0000")   # low 24% / high 100%
BAND_WORKING = SlotBand(steady="#002A80", flash_peak=None)      # medium 50%
BAND_DONE = SlotBand(steady="#003D18", flash_peak="#008033")    # low 24% / medium 50%
```

| Rank | `AgentMode` | Band | First 5 minutes | After 5 minutes |
|---|---|---|---|---|
| 1 | `BLOCKED_ERROR`, `WAITING_FOR_INPUT` | alert | red, low↔high blink | red, low steady |
| 2 | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` | working | blue, medium steady | unchanged |
| 3 | `COMPLETED` | done | green, low↔medium blink | green, low steady |
| 4 | `IDLE_READY` | done | green, low↔medium blink | green, low steady |
| — | `UNKNOWN` | — | holds no slot | holds no slot |

Blocked and waiting-for-input share rank 1, because the source request listed
them as tied for importance; the table renumbers below them. Rank 3 and
rank 4 now render identically and differ only in eviction priority. That is
almost moot in practice: `IDLE_VISIBLE_SECONDS` is `0.0` (`collector.py:37`), so
an idle status is stale by the collection after the one that created it and an
idle slot will almost never appear. The rank stays so the eviction ordering is
total.

Empty slots are `#000000`.

### Why blue does not settle

The five-minute threshold is the prompt cache TTL, and that is the whole reason
it exists: flashing means the cache behind that session is probably still warm,
so picking it up now is cheap, and steady means resuming will pay full price. An
actively running agent has no such story — it is running, and it will still be
running in six minutes. There is nothing for a settle to signal.

The cost is real and accepted: a session working for an hour looks identical to
one that started two seconds ago. In exchange, working sessions trigger no
five-minute rewrite, and a strip where everything is working renders as one
static line. Working is the boring state and should look boring.

### Why the hex values are provisional

Numeric intensity is not perceived intensity. On RGB LEDs a pure blue at half
scale reads very differently from a red at half scale, so one shared tier
fraction will not look like one tier. All five constants live in a single table
precisely so that tuning them requires touching nothing else.

One known edge: the low tier combined with a low slider setting can quantize to
invisible — at slider 25% `#3D0000` becomes an effective red of roughly 15/255.
Accepted rather than floored. Dimming the strip to nearly nothing is a reasonable
thing to want from a brightness slider, and a floor would surprise the user who
asked for it.

## Program shape

The flash inverts. Today line 1 holds each slot's colour and line 2 pulses it
*down to black*; the new settled state is a dimmer colour, so line 1 becomes the
settled colour and line 2 pulses *up to the peak*.

```text
0:#3D0000 1:#3D0000 2:#002A80 3:#003D18 4:#003D18 5:#000000 6:#000000 7:#000000
0:#FF0000 900ms pulse; 3:#008033 900ms pulse
repeat 328
```

Slot 0 is a fresh `WAITING_FOR_INPUT` blinking low↔high red, slot 1 the same
state gone quiet, slot 2 working, slot 3 freshly `COMPLETED`, slot 4 a completed
session that has settled. `pulse` returns to the line's start colour, so each
cycle is a blink against the baseline; unmentioned LEDs hold, so settled slots are
rock steady.

Slot 2 is the load-bearing one. It is as freshly changed as slot 0 and it still
does not appear on the flash line, because `BAND_WORKING.flash_peak` is None.

The renderer change is one expression: the flash line's target becomes
`slot.flash_color` instead of the constant `SLOT_EMPTY`.
`slot_repeat_count`, `SLOT_FLASH_PULSE_MS = 900`, the measured
`SLOT_FLASH_CYCLE_MS = 917`, and the `[1, 65535]` clamp are unchanged.

Worst case — eight alert slots all flashing, `repeat 65535`, brightness directive
present — is roughly 290 bytes across 4 lines, inside the 512-byte / 20-line
limits `validate_led_text` enforces.

Two constraints inherited from the original design and still binding: `repeat N`
caps at 65535, and `0:off` is rejected with `bad-index`, so indexed segments need
an explicit `#000000`.

### The inversion is why the settle is free

`repeat` is program-global, so per-slot five-minute timers are not expressible in
one program. The host still sizes `N` to the longest remaining flash and drops
each slot from line 2 as it crosses its own five-minute mark.

What changes is where `repeat` expiry lands. Today it leaves the device holding
the *bright* colour, which is the pre-settle appearance, so the strip is wrong
until the host rewrites. Now it leaves the device holding line 1, which already
*is* the settled appearance. Two consequences:

- If the status-bar app dies, the strip settles correctly on its own instead of
  freezing on a state that has expired.
- The five-minute write is visually a no-op. It is still one write — the dedupe
  key changes when `flash_color` drops to `None` — but the emitted baseline is
  byte-identical to the previous baseline.

A flashing slot is also never fully dark now, since it oscillates dim↔bright
rather than bright↔off.

### Verify before implementing

The original design proved its program shape against `resources/sdled.wasm` — the
same parser the firmware and web simulator run — through a node harness driving
`sdled_parse`/`sdled_step`. That is how the `repeat` cap and the `0:off`
rejection surfaced.

Two properties this shape depends on are documented in `LEDS_FORMAT.md` but not
measured here: that `pulse` returns to the line's start colour when the target is
*brighter* than that start, and that LEDs unmentioned on line 2 hold line 1's
colours. Re-running that harness against the new shape is the first
implementation step, ahead of any renderer code.

## Data shape

`Slot` carries the pair. `flash_color` is keyword-only, and that is deliberate:
there are roughly forty positional `Slot(SLOT_AMBER, True, FLASH_SECONDS)` calls
in the test suite, and if argument two quietly changes from `bool` to
`str | None`, a stale `True` stays truthy, `.flashing` reports `True`, and the
renderer emits `0:True 900ms pulse` — caught only where a test asserts exact
program text. Keyword-only turns every stale call site into a `TypeError` at
collection.

```python
@dataclass(frozen=True)
class Slot:
    color: str
    flash_color: str | None = field(default=None, kw_only=True)
    remaining_flash_seconds: float = field(default=0.0, kw_only=True)

    @property
    def flashing(self) -> bool:
        return self.flash_color is not None
```

`remaining_flash_seconds` is keyword-only too, for the same reason: it was
argument three, so leaving it positional would let a two-argument stale call
shift silently into it.

`SlotBand` carries a `slot(remaining_flash_seconds=0.0)` constructor returning
the `Slot` that band renders. `_render` needs exactly that flashing-or-not
decision, and roughly forty test call sites need a way to say "an alert slot with
a full window left" without restating three keyword arguments, so it is one method
both use rather than the same logic duplicated per site.

Keeping `.flashing` as a derived property is what limits the blast radius:
`cli.py:1101` and `slot_repeat_count` need no edit.

`_Occupant` stores the band rather than a colour string, so the flash clock
resets on a band change. `WORKING` → `TOOL_RUNNING` does not reset it, as today.
`BLOCKED_ERROR` → `WAITING_FOR_INPUT` and `COMPLETED` → `IDLE_READY` also do not,
which was already true when those pairs merely shared a hex.

One of those three is no longer observable through `Slot`. The working band has
no `flash_peak`, so a working slot renders `remaining_flash_seconds = 0.0` at any
age and the clock behind it is invisible from outside. The guarantee still holds
— `band != occupant.band` is false for that pair — but a test has to reach
`SlotAllocator._slots[index].entered_at` to see it. Accepted: it is a private
read inside the module's own test file, and the alternative is asserting an
invariant nobody can check.

`slot_render_key` extends from `(brightness, *(color, flashing))` to
`(brightness, *(color, flash_color))`. No two bands share a steady colour today,
so this is belt-and-braces; it costs nothing and it stops a future band split
from deduping wrong. It still excludes the repeat count, because remaining flash
time falls on every refresh and keying on it would rewrite `LEDS.LED` every 15
seconds forever.

## Brightness

Intensity now lives in the colours, so the global directive is free to carry the
user's slider.

`SLOT_BRIGHTNESS` moves from 128 to 255. Leaving it at 128 would halve the whole
scheme — "high intensity red" would render at 128. At 255 `apply_brightness`
omits the directive entirely, so `sidepulse agent-monitor leds --display
multiagent`, which relies on the controller default, emits no `brightness` line
at all.

The status bar assigns `controller.brightness = device.brightness`. The settings
default is also 255 (`settings.py:131`), so the strip matches the slider from the
first write. The two submenu items the last change removed come back: the
`Brightness N%` readout and the slider, by dropping the
`display != LED_DISPLAY_MULTIAGENT` guard at `status_bar.py:2504-2506`.

The slider is a master gain over the bands, not a replacement for them. Final
output is hue × band tier × slider, so tier relationships hold at any slider
position.

## Seams

| File | Change |
|---|---|
| `src/sidepulse/led_slots.py` | `SlotBand` (with a `slot()` constructor) + three band constants replace `SLOT_AMBER`/`SLOT_CYAN`/`SLOT_GREEN`; `MODE_SLOT_BAND` and `slot_band()` replace `MODE_SLOT_COLOR` and `slot_color()`; `FLASHING_RANKS` deleted; `Slot.flash_color` added keyword-only with `.flashing` derived; `_Occupant.color` → `_Occupant.band` |
| `src/sidepulse/led_status.py` | flash-line target → `slot.flash_color`; `SLOT_BRIGHTNESS` 128 → 255; `slot_render_key` carries the colour pair |
| `src/sidepulse/status_bar.py:1776` | assign `controller.brightness = device.brightness`; drop the "not wired up yet" comment |
| `src/sidepulse/status_bar.py:1970` | pass `brightness=device.brightness` to the Screen Bar's `program_for_slots` call, which omits it today |
| `src/sidepulse/status_bar.py:2500-2506` | drop the `!= LED_DISPLAY_MULTIAGENT` guard on the brightness readout and slider |
| `src/sidepulse/cli.py` | none — the `SLOT_EMPTY` comparison and the `.flashing` count both still hold |

`MODE_SLOT_RANK`, `slot_rank`, `SLOT_EMPTY`, `FLASH_SECONDS`, and the allocator's
assign/hold/release/evict body are untouched.

### The Screen Bar needs the brightness too

`virtual_device.py` itself needs no edit — it renders by running program text
through `led_wasm`, so it picks up the new bands for free. But the call site that
*builds* that text does need one. `sync_virtual_status_device` renders the
multiagent Screen Bar with a bare `program_for_slots(slots)`, while the battery
and aggregate branches on either side of it both pass
`brightness=device.brightness`.

Left alone, that omission has two consequences here that it did not have before:
`SLOT_BRIGHTNESS` 128 → 255 doubles the Screen Bar's output, and once the physical
strip follows the slider the Screen Bar permanently does not. The second is the
one that matters, because `docs/usage.md` promises "Colours agree, but nothing
else is guaranteed" about the Screen Bar, and that would stop being true.

## Documentation

`docs/usage.md` carries five things that go stale and must move with the code:

- the colour table and flash description at `:11-30` (amber/cyan/green with the
  old hexes, and "Brightness is 128"),
- the "Per-device brightness is ignored in this mode" section at `:139-146`, which
  promises a follow-up that this change ships,
- "turns the parent's slot amber" at `:166`,
- the "Colours agree, but nothing else is guaranteed" claim in the Screen Bar
  section at `:168-179`, which the brightness fix above makes true again,
- the sample program and its pulse-to-black explanation at `:251-270`.

`docs/usage.md:8` points its rationale link at the 2026-08-24 design doc. After
this change the colour and brightness rationale is one hop further away, behind a
superseded banner, so that link should name both documents.

The 2026-08-24 design doc gets a superseded-by pointer at its top rather than an
edit; its state table and program shape are now wrong and a reader should be sent
here instead.

`README.md:94-101` describes aggregate mode and stays as it is.

## Testing

TDD, tests before implementation, starting with the `led_wasm` harness check
described above.

New behaviour (`tests/test_led_slots.py`):

- Each `AgentMode` maps to the expected band; `slot_rank` values are unchanged
  from today.
- A working slot never flashes, at any age — the inverse of today's "idle never
  flashes".
- An idle slot flashes green, the same as a completed one.
- Alert and done slots flash to their peak, then settle to `steady` with
  `flash_color is None`.
- The flash clock resets on a band change and survives
  `BLOCKED_ERROR` → `WAITING_FOR_INPUT`. For `WORKING` → `TOOL_RUNNING` the
  assertion reads `_slots[index].entered_at`, per the note above.
- Every flashing band's peak is brighter than its steady colour. The hexes are
  provisional; this is the invariant the retuning must not break.

Rendering (`tests/test_sidepulse.py`):

- Exact program text for the mixed vector in the Program shape section, including
  that its freshly-changed working slot is absent from the flash line.
- Eight alert slots all flashing passes `validate_led_text` — the byte and line
  worst case, measured at 288 bytes.
- An all-working strip renders one baseline line, no flash line, no `repeat`.
- The default emits no directive; a slider below full emits `brightness N`.
- Empty slots render `#000000`, never `N:off`.
- Stepping a rendered program through `sdled.wasm` never takes a flashing slot
  below its steady colour, and leaves it on that colour past the repeat window.

Controller:

- An unchanged vector produces no write.
- Crossing five minutes produces exactly one write — and no further write on the
  refresh after it — with a baseline byte-identical to the previous program's.
- A slider change produces one write.

Replaced:

- `test_status_bar_multiagent_submenu_omits_the_inert_brightness_controls`
  inverts — the readout and slider are present, and the controller receives
  `device.brightness`.
- `test_multiagent_controller_writes_once_when_a_slot_settles` already exists and
  must be edited in place rather than added. Redefining the name silently drops
  the original, whose fourth phase is what proves the settle writes once.
- `test_multiagent_program_uses_full_brightness_without_a_directive` is superseded
  by the new default-brightness test and deleted.

Rewritten mechanically:

- Every `SLOT_AMBER`/`SLOT_CYAN`/`SLOT_GREEN` reference in
  `tests/test_led_slots.py` and `tests/test_sidepulse.py` becomes the
  corresponding band's `steady` or `flash_peak`. Keyword-only `flash_color` makes
  any missed site fail at collection rather than render wrong.

The eviction and sub-agent-coalescing tests carry over unchanged. That is the
check that this stayed a presentation change.

## Sleep and power loss

Unchanged from the original design, with one improvement. The Pro is bus-powered
by the SD reader and loses power when the machine sleeps, so `repeat` counts do
not survive a power cycle and the device replays `INIT.LED`. Slot assignments
survive sleep because allocator state lives in the app's memory; flash clocks are
wall-clock and expire during it, which is correct — a session that was waiting
when the lid closed has in fact been waiting a long time.

The improvement: because `repeat` expiry now lands on the settled appearance
rather than the pre-settle one, every path that leaves the device running without
a host rewrite — sleep, app death, a missed `/Volumes` signature change — ends on
a state that is true rather than one that has expired.

## Open questions

None outstanding. The hex values are provisional by design, to be tuned on
hardware once the bands are in place.
