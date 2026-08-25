# Multiagent LED slots

Date: 2026-08-24
Status: accepted, implementing

> **Superseded in part.** The state table, program shape and brightness handling
> here were replaced by
> [`2026-08-25-multiagent-led-colour-bands-design.md`](2026-08-25-multiagent-led-colour-bands-design.md).
> The strip now carries three colour bands with their own intensity, the flash
> pulses up from the settled colour rather than down to black, and the brightness
> slider is live. Everything else in this document — slot allocation, eviction,
> the status feed, sleep behaviour, the write budget — still holds.

## Summary

Add a third `led_display` kind, `multiagent`, that treats the strip as N
independent slots — one LED per agent session — instead of one aggregate
indicator. Colour encodes what that session needs. A slot flashes for the first
five minutes after it enters a state, then holds steady, so "changed recently"
is distinguishable from "has been like this a while" at a glance.

## Goals

- One LED = one agent session, with a sticky slot so position means something.
- Read "which of my sessions is waiting on me" and "which are running" without
  reading any text.
- Degrade sanely when there are more sessions than LEDs.
- Add no new polling. The status bar already refreshes on a 15 s timer
  (`status_bar.py:236`) and is push-fed by the hook socket; this feature adds no
  timer and writes `LEDS.LED` only when the rendered slot vector changes.

## Non-goals

- Sub-agents (Task-tool children) as separate slots. A slot is a top-level
  session. This does *not* fall out of `HookEvent.status_key` (`models.py:59`) —
  that key prefers `agent_id` over `session_id`, which is precisely what gives a
  child a key distinct from its parent's. Folding them together is explicit
  work, done in `collector.multiagent_statuses` before the allocator sees
  anything, on the same session key the menu coalesces on.
- Replacing aggregate mode. `agent` stays the default and stays the right choice
  for the 2-LED Dot.

## What changes from today

Today `led_display: "agent"` funnels every session through `aggregate_status`
(`collector.py:1173`): N sessions collapse to one winning `AgentMode`, then to
one of four `LedDisplayState`s, then to a whole-strip program. `WORKING` is a
chase animation that consumes all eight LEDs (`led_status.py:104`).

In `multiagent` the aggregate stops driving the LEDs. The renderer consumes
`collector.multiagent_statuses` — `snapshot.statuses` plus the `COMPLETED`
sessions the aggregate path hides while anything is active, with sub-agents folded
onto their parent — and emits one program describing all slots. `BatteryLedController.sync_snapshot` is the
existing precedent for a controller taking richer state than a single enum, so
this mirrors it rather than inventing a shape.

## State table

Importance rank drives eviction. Rank 1 is most important.

| Rank | `AgentMode` | Colour | Flashes when recent |
|---|---|---|---|
| 1 | `BLOCKED_ERROR` | amber `#FF3A00` | yes |
| 1 | `WAITING_FOR_INPUT` | amber `#FF3A00` | yes |
| 2 | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` | cyan `#00E5FF` | yes |
| 3 | `COMPLETED` | green `#00FF66` | yes |
| 4 | `IDLE_READY` | green `#00FF66` | no |
| — | `UNKNOWN` | not displayed, holds no slot | — |

Ranks 1 and 2 in the source request were written as "1 & 2 are tied for
importance"; blocked and waiting-for-input therefore share rank 1 here and the
table renumbers below them.

Brightness is 128 ("medium intensity"), applied via the existing
`brightness N` directive, which `apply_brightness` already emits.

Empty slots are `#000000`.

### `IDLE_READY` is effectively unreachable

`IDLE_READY` is listed for completeness but will almost never light a slot.
`status_is_stale` treats an idle status as stale once `age > idle_visible_seconds`
(`collector.py:1266`), and `IDLE_VISIBLE_SECONDS` is `0.0`
(`collector.py:37`) — not configurable through settings anywhere. An idle status
therefore drops out of `snapshot.statuses` on the collection after the one that
created it, and the renderer only ever sees fresh statuses.

So in practice the strip has three colours: amber, cyan, green. Rank 4 stays in
the table so the eviction ordering is total and so raising
`idle_visible_seconds` later needs no new design, but no implementation should
depend on an idle slot appearing.

### `COMPLETED` vs `IDLE_READY`

`COMPLETED` means a turn finished and its output is waiting to be read — from
`Stop`/`SubagentStop` with no question detected, `SessionEnd`, a completion
`Notification`, or an explicit `done` marker (`collector.py:1053-1070`). It stays
visible for 20 minutes (`COMPLETED_VISIBLE_SECONDS`).

`IDLE_READY` means a session exists and has done nothing yet — only
`SessionStart`, or an explicit `idle`/`ready` marker. It is not "finished", it is
"never started".

## Program shape

Verified against `resources/sdled.wasm` — the same parser the firmware and web
simulator run — through a node harness driving `sdled_parse` / `sdled_step`.

```text
brightness 128
0:#FF3A00 1:#FF3A00 2:#00E5FF 3:#00E5FF 4:#00FF66 5:#00FF66 6:#000000 7:#000000
0:#000000 900ms pulse; 2:#000000 900ms pulse; 4:#000000 900ms pulse
repeat 328
```

Line 2 is the baseline: every slot's steady colour. Line 3 dips only the
recently-changed slots to black and back — `pulse` returns to the line's start
colour, so each cycle is a blink against the baseline. Unmentioned LEDs hold, so
settled slots are rock steady. After the repeat count is exhausted the device
holds the final state, which is the baseline, i.e. steady.

Measured: 173 bytes, 4 lines — well inside the 512-byte / 20-line limits
`validate_led_text` enforces. Stepping the program confirms slots 0/2/4 blink
(`801d00 → 000000 → 801d00`), slots 1/3/5/6/7 never move, and every sample past
the repeat count is the steady baseline.

Two parser constraints found the hard way:

- `repeat N` caps at 65535; `repeat 99999` fails with `bad-repeat`. Five minutes
  at the measured 917 ms cycle is 328, so there is ample headroom.
- `0:off` is rejected with `bad-index`. Indexed segments need an explicit
  `0:#000000`.

### Why the host still participates

`repeat N` is program-global, so per-slot independent five-minute timers are not
expressible in a single program. The rule that avoids both failure modes:

Set `N = ceil(longest_remaining_flash_ms / cycle_ms)`, clamped to `[1, 65535]`.
Nothing stops flashing early. As each slot individually crosses its five-minute
mark, the next refresh notices the slot's flash phase changed, and rewrites with
that slot dropped from line 3.

This gives a good failure mode: if the status-bar app dies, the device stops
flashing on its own instead of blinking forever.

Any rewrite restarts the blink phase for all flashing slots. Because the flash
is a repeating blink rather than a one-shot ramp, restarting mid-blink is
visually harmless; the only thing that matters is which slots are on line 3, and
the host controls that exactly.

## Slot allocator

New module `src/sidepulse/led_slots.py`. Standard library only, no PyObjC, no
AppKit — it is pure state plus arithmetic and is unit-testable in isolation.

State is a `status_key → slot index` map plus, per slot, the timestamp at which
its current display colour was entered.

- **Assign.** A session that becomes displayable and holds no slot takes a
  uniformly random free slot. Random rather than left-packed, per the request:
  position is arbitrary but sticky, so you learn "my sidepulse session is the
  third one" by repetition rather than by ordering rule.
- **Hold.** A session keeps its slot while it stays displayable, across state
  and colour changes.
- **Release.** A session's slot is freed when it leaves the set the allocator
  was fed — it ended, or it aged past its visibility window. Note that being
  demoted into `stale_statuses` is *not* one of those routes: the aggregate path
  demotes finished sessions as soon as anything is active, and
  `multiagent_statuses` puts them back.
- **Evict.** When no slot is free, a newcomer evicts the occupant of strictly
  lowest importance, and only if the newcomer is strictly more important than
  that occupant. Ties break on oldest state-entry timestamp. A newcomer no more
  important than every occupant gets no slot and is not displayed.

Randomness is injected as a `random.Random` instance so tests seed it and assert
exact placement.

### Flash clock

Each slot records when its current colour was entered. Entering a new colour
resets the clock; a state change within the same colour band (`WORKING` →
`TOOL_RUNNING`, both cyan) does not. A slot flashes while
`now - entered_at < 5 min` and its rank is 1–3. Rank 4 (idle) never flashes.

Five minutes is the prompt cache TTL, and that is the whole point of the
threshold: flashing means the cache behind that session is probably still warm,
so picking it up now is cheap. Steady means the window has passed and resuming
will pay full price. This is why the flash deliberately settles rather than
nagging until answered — once the cache is cold, the urgency the flash was
signalling is gone, and a slot that blinks forever would be telling you
something that is no longer true.

## Sleep and power loss

Accepted behaviour, not a defect to design around. Recorded here so it is not a
surprise later.

The Pro is bus-powered by the built-in SD reader, so when the machine sleeps the
device loses power and the strip goes dark. `LEDS_FORMAT.md` documents both
halves of this: the reader "can power off SidePulse Pro after 3 minutes of
inactivity" (the reason `keep_awake.py` touches `keepalive`), and `INIT.LED` is
"played on power up" — a device that replays a startup program has lost its
state. `sd_eject_guard.c:1-8` exists because the SD slot re-enumerates after a
hibernate wake. The Dot is USB-C and may retain power through sleep; neither
behaviour has been tested against hardware here.

Consequences specific to this design:

- **The `repeat` count does not survive a power cycle.** The device counts
  repeats on its own clock. Power off mid-program and the flash phase is gone
  entirely. This is self-correcting, because the host recomputes remaining flash
  time from `AgentStatus` timestamps on the next write.
- **Recovery is incidental and can be missed.** There are no
  `NSWorkspaceWillSleep`/`DidWake` observers in `src/`. The only thing that
  invalidates the controller's dedupe cache is `observe_connected_devices`
  (`status_bar.py:1786`), which hashes a `/Volumes` connection signature and
  calls `reset_led_controllers_for_device` when an entry changes. If the device
  loses power *without* the mount signature changing, the cache still believes
  the strip is correct, `sync` returns early, and the strip sits on `INIT.LED`
  until some agent genuinely changes state. Aggregate mode has this same gap
  today; multiagent inherits it unchanged and does not make it worse.
- **Slot assignments survive sleep, flash clocks expire during it.** Allocator
  state lives in the app's memory and the app is not killed by sleep, so slots
  keep their positions. Flash clocks are wall-clock, so a session that was
  waiting when the lid closed renders steady on wake. That is correct: it has in
  fact been waiting a long time, and per the prompt-cache rationale above its
  cache is cold.

## Write budget

The controller dedupes on the full rendered key: per-slot `(colour, flashing)`
plus brightness. `AgentLedController` already has this shape
(`led_status.py:211`); the multiagent controller extends the key from a single
enum to the slot vector.

Consequences: no hook events and no boundary crossings means zero writes, even
though the 15 s refresh keeps firing. A genuine state change is one write. A
slot settling at five minutes is one write, landing within 15 s of the true
boundary — imprecision that does not matter for a recency cue. No new timer is
added.

## Seams

| File | Change |
|---|---|
| `src/sidepulse/led_slots.py` | new: allocator, flash clock, rendered-slot vector |
| `src/sidepulse/led_status.py` | new `program_for_slots(...)` and `MultiAgentLedController.sync_statuses(...)` alongside the existing aggregate path |
| `src/sidepulse/settings.py` | `LED_DISPLAY_MULTIAGENT = "multiagent"` added to `LED_DISPLAY_CHOICES` |
| `src/sidepulse/status_bar.py:1953` | select the multiagent controller and pass `snapshot.statuses` instead of `mode` |
| `src/sidepulse/cli.py:763` | same for `sidepulse agent-monitor leds` |

Per-device display selection already exists (`settings.display_for_device`), so
the Pro can run `multiagent` while the Dot stays on aggregate `agent` — two
slots is not enough to be worth the mode, and that split needs no new
configuration surface.

`virtual_device.py` renders the Screen Bar by running the program text through
`led_wasm`, so the virtual device gets multiagent rendering with no extra work.

Adding a display kind touches the four layers CLAUDE.md calls out, but not the
mode/state enums: `AgentMode` and `LedDisplayState` are unchanged. The new code
maps modes to slot colours directly and does not route through
`display_state_for_mode`, which exists to collapse to four whole-strip states.

## Testing

TDD, tests before implementation.

Allocator (`tests/test_led_slots.py`, pure, no device, no AppKit):

- A new session takes a free slot; seeded `Random` makes the index exact.
- A session holds its slot across colour changes.
- Releasing on disappearance frees the slot; the next newcomer can take it.
- Full strip: a rank-1 newcomer evicts a rank-4 occupant.
- Full strip: a rank-4 newcomer with all slots at rank 1 is not displayed.
- Eviction ties break on oldest state-entry timestamp.
- Flash clock resets on colour change, not on same-band state change.
- Idle never flashes.

Program rendering (`tests/test_sidepulse.py`, following the existing
`program_for_display_state` assertions):

- Exact program text for a representative mixed vector.
- Every rendered program passes `validate_led_text` at all 8 occupied slots and
  all flashing — the worst case for bytes and lines.
- Repeat count is `ceil(longest_remaining / cycle)` and clamps at 65535.
- Empty slots render `#000000`, never `N:off`.
- Brightness 128 emits the `brightness` directive.

Controller:

- No write when the slot vector is unchanged.
- One write when a slot crosses the five-minute boundary.
- Device write errors are retried on the existing `error_retry_seconds` backoff.

If PyObjC is present, the existing `led_wasm` harness should parse every program
the renderer produces. That is the check that catches DSL mistakes the size-only
`validate_led_text` cannot — it is how the `repeat` cap and the `0:off`
rejection surfaced.

## Open questions

None outstanding.
