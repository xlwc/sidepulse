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
  `multiagent_controller_for_device` (`status_bar.py:1777`) currently refuses on
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
0:#3D0000 1:#3D0000 2:#002A80 3:#003D18 4:#000000 5:#000000 6:#000000 7:#000000
0:#FF0000 900ms pulse; 3:#008033 900ms pulse
repeat 328
```

Slot 0 is a fresh `WAITING_FOR_INPUT` blinking low↔high red, slot 1 the same
state gone quiet, slot 2 working, slot 3 freshly `COMPLETED`. `pulse` returns to
the line's start colour, so each cycle is a blink against the baseline;
unmentioned LEDs hold, so settled slots are rock steady.

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
    flash_color: str | None = None
    remaining_flash_seconds: float = 0.0

    @property
    def flashing(self) -> bool:
        return self.flash_color is not None
```

Keeping `.flashing` as a derived property is what limits the blast radius:
`cli.py:1101` and `slot_repeat_count` need no edit.

`_Occupant` stores the band rather than a colour string, so the flash clock
resets on a band change. `WORKING` → `TOOL_RUNNING` does not reset it, as today.
`BLOCKED_ERROR` → `WAITING_FOR_INPUT` and `COMPLETED` → `IDLE_READY` also do not,
which was already true when those pairs merely shared a hex.

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
`display != LED_DISPLAY_MULTIAGENT` guard at `status_bar.py:2500`.

The slider is a master gain over the bands, not a replacement for them. Final
output is hue × band tier × slider, so tier relationships hold at any slider
position.

## Seams

| File | Change |
|---|---|
| `src/sidepulse/led_slots.py` | `SlotBand` + three band constants replace `SLOT_AMBER`/`SLOT_CYAN`/`SLOT_GREEN`; `MODE_SLOT_BAND` and `slot_band()` replace `MODE_SLOT_COLOR` and `slot_color()`; `FLASHING_RANKS` deleted; `Slot.flash_color` added keyword-only with `.flashing` derived; `_Occupant.color` → `_Occupant.band` |
| `src/sidepulse/led_status.py` | flash-line target → `slot.flash_color`; `SLOT_BRIGHTNESS` 128 → 255; `slot_render_key` carries the colour pair |
| `src/sidepulse/status_bar.py:1777` | assign `controller.brightness = device.brightness`; drop the "not wired up yet" comment |
| `src/sidepulse/status_bar.py:2500` | drop the `!= LED_DISPLAY_MULTIAGENT` guard on the brightness readout and slider |
| `src/sidepulse/cli.py` | none — the `SLOT_EMPTY` comparison and the `.flashing` count both still hold |

`MODE_SLOT_RANK`, `slot_rank`, `SLOT_EMPTY`, `FLASH_SECONDS`, and the allocator's
assign/hold/release/evict body are untouched. `virtual_device.py` renders the
Screen Bar by running program text through `led_wasm`, so it picks up the new
bands with no edit.

## Documentation

`docs/usage.md` carries four things that go stale and must move with the code:

- the colour table at `:15-17` (amber/cyan/green with the old hexes),
- the "always renders at brightness 128 … until then the device submenu omits
  both" section at `:141-144`,
- "turns the parent's slot amber" at `:166`,
- the pulse-to-black explanation of the program shape.

The 2026-08-24 design doc gets a superseded-by pointer at its top rather than an
edit; its state table and program shape are now wrong and a reader should be sent
here instead.

`README.md:97-102` describes aggregate mode and stays as it is.

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
  `WORKING` → `TOOL_RUNNING` and `BLOCKED_ERROR` → `WAITING_FOR_INPUT`.

Rendering (`tests/test_sidepulse.py`):

- Exact program text for the mixed vector in the Program shape section.
- Eight alert slots all flashing passes `validate_led_text` — the byte and line
  worst case.
- An all-working strip renders one baseline line, no flash line, no `repeat`.
- Brightness 255 emits no directive; 128 emits `brightness 128`.
- Empty slots render `#000000`, never `N:off`.

Controller:

- An unchanged vector produces no write.
- Crossing five minutes produces exactly one write, and the emitted baseline is
  byte-identical to the previous one.
- A slider change produces one write.

Replaced:

- `test_status_bar_multiagent_submenu_omits_the_inert_brightness_controls`
  inverts — the readout and slider are present, and the controller receives
  `device.brightness`.

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
