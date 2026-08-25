# Multiagent LED slots — usage

One LED per agent session instead of one aggregate indicator. This is the
`multiagent` value for `led_display`, added alongside `agent`, `battery` and
`custom`.

Design rationale lives in
[`2026-08-24-multiagent-led-slots-design.md`](superpowers/specs/2026-08-24-multiagent-led-slots-design.md)
for slot allocation, eviction and the status feed, and in
[`2026-08-25-multiagent-led-colour-bands-design.md`](superpowers/specs/2026-08-25-multiagent-led-colour-bands-design.md)
for the colour bands, the flash shape and brightness.
This file is the operational half: how to turn it on, and what will surprise you.

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

## Turning it on

### Per device, from the menu bar (recommended)

Menu bar icon → your device's submenu → **One LED Per Agent**. Back to aggregate
with **Agent Status** in the same radio group.

This is the only route that needs no restart. It saves settings, resets the
device's controller and re-renders immediately.

### Globally, from the CLI

```sh
sidepulse battery configure --display multiagent   # on
sidepulse battery configure --display agent        # off
```

Living under `battery` is odd, but that subcommand owns the global `led_display`
setting for all display kinds.

**This may do nothing.** See the precedence gotcha below.

### One-shot, without persisting anything

```sh
# Print the LED program instead of writing it
sidepulse agent-monitor leds --once --dry-run --display multiagent

# Drive a device directly, no settings involved
sidepulse agent-monitor leds --display multiagent
```

Good for seeing the program text and confirming which sessions are live.
`--display` defaults to `agent`, so existing invocations are unchanged.

## Gotchas

### Per-device settings silently beat the global one

`AgentMonitorSettings.display_for_device` checks for a remembered device entry
first and only falls back to the global `led_display`. Any device you have ever
set a display mode on has its own entry, so `battery configure --display` will
not move it.

Check what is actually in effect. This reads the file directly, so any `python3`
will do — no interpreter with `sidepulse` installed needed:

```sh
python3 -c "
import json, os
p = os.path.expanduser('~/.config/sidepulse/agent-monitor/settings.json')
d = json.load(open(p))
print('global:', d.get('led_display'))
for dev in d.get('devices', []):
    print(' ', dev['id'], '->', dev['led_display'])
"
```

```text
global: agent
  /Volumes/SidePulse -> multiagent
```

That device entry wins, so the global `agent` is irrelevant to it. Note the JSON
key is `id`, not `device_id`.

If the device you care about is listed, use the menu item or edit that entry.

### The running app reads settings once, at launch

The 15 s refresh timer never re-reads `settings.json`. Every `load_settings()`
call after `__init__` is an error rollback inside an `except` clause.

So a hand-edited `settings.json`, or `battery configure`, needs a restart:

```sh
launchctl kickstart -k gui/$(id -u)/io.sidepulse.agentstatus
```

Do **not** use `sidepulse status-bar stop && sidepulse status-bar start` as a
restart — `stop` sets `uninstall = True` and removes the LaunchAgent plist
rather than restarting the process.

### Hand-editing settings.json while the app runs loses your edit

`set_device_display` builds the new settings from the app's **in-memory** copy
and saves that over the file. Edit `settings.json` while the app is running,
then touch any menu setting afterwards, and your edit is silently reverted.

Edit while stopped, or use the menu.

### A code change needs a process restart, not just a settings change

If your install is editable (`pip install -e`), new code is on disk immediately
but the running process still has the old code in memory — the menu item will be
missing until you kickstart. Confirm which tree the app's interpreter imports:

```sh
python3 -c "import sidepulse, sidepulse.settings as s
print(sidepulse.__file__)
print('multiagent available:', 'multiagent' in s.LED_DISPLAY_CHOICES)"
```

Run that with the *same* interpreter the LaunchAgent uses — check with
`ps aux | grep 'sidepulse status-bar'`, which is not necessarily the `python3` on
your `PATH`.

### Turning the slider down can hide the settled colours

Per-device brightness works in this mode: the slider emits `brightness N`, which
scales all three bands together. The settled colours are already the dim end of
each band, so at a low slider setting they can quantize to nearly nothing — at
25%, `#3D0000` is an effective red of about 15/255.

That is accepted rather than floored. Dimming the strip to almost nothing is a
reasonable thing to want from a brightness slider, and a floor would surprise
whoever asked for it. If settled slots are too faint for you, raise the slider or
raise the `steady` values in `led_slots.py`.

If you used this mode before the slider was wired up, expect a one-off surprise:
the strip ignored your brightness setting and rendered at a fixed 128, so
whatever the slider was left at now applies for the first time. A strip that
looks too dark after upgrading is almost always this, and the submenu now shows
the percentage next to the slider, so it is one drag to fix.

### Slot positions are sticky per process, not persistent

A session keeps its slot for as long as it stays visible, so you learn "mine is
the third one" by repetition. Positions are assigned at random, and allocator
state lives only in memory — restart the app and everything reshuffles. Slots
survive sleep/wake, because the app is not killed by sleep. They also survive
every event that merely makes the app rewrite `LEDS.LED`: a battery preview on a
power change, a brightness edit, a lid animation, a `/Volumes` signature change.
Those reset the controller's belief about what the strip shows, which is what
forces the rewrite, and nothing else — the slot map is left alone.

### Sub-agents share their parent's LED

A Claude session running Task-tool children reports a status per child, each with
its own `agent_id`, so the raw status set has more entries than there are
sessions. `collector.multiagent_statuses` folds them onto the parent's session
key before the allocator runs, and the surviving status is the most important of
the group. Three parallel sub-agents therefore light one LED, not four — and a
child stopped on a permission prompt turns the parent's slot red.

### The Screen Bar can disagree with the physical strip

`sync_virtual_status_device` renders from the mode rather than reusing the
strip's program, so it has its own allocator, seeded independently. Hue agrees —
both call `program_for_slots` with the same bands — but nothing else is
guaranteed:

- **Positions differ.** Each allocator builds its own `random.Random`, so the same
  session sits at a different index on the Screen Bar than on the strip. This is
  true from first use, not something that drifts.
- **Counts can differ.** `virtual_slot_allocator` is fixed at 8 slots, while a
  physical device's count comes from `led_count_for_target`. Four live sessions
  light four Screen Bar slots and two LEDs on a Dot.
- **Intensity can differ.** Each display passes its own device's brightness, and
  the Screen Bar has no slider — it is a separate `settings.json` entry with no UI
  to move it, so it stays at full while the strip follows yours. Turn the strip
  down and the Screen Bar will not follow. This is not new to this mode: the
  battery and aggregate displays have always worked the same way.

### On a Dot, six of eight sessions would be invisible

Slot count comes from `led_count_for_target`, which matches `sidepulsepro` /
`sidepulsedot` in the volume name and otherwise falls back to 8. A 2-LED Dot
named something else therefore gets 8 slots, and sessions in slots 2–7 go
nowhere. The fallback is shared with aggregate mode, but this is the mode where
it bites. `multiagent` is a Pro feature; the Dot is better left on `agent`.

### Sleep blanks the strip and recovery can be missed

The Pro is bus-powered by the SD reader, so sleep cuts power and the strip
replays `INIT.LED` on wake. The controller dedupes writes, so if the `/Volumes`
mount signature did not change it still believes the strip is correct and the
strip stays on `INIT.LED` until some agent genuinely changes state. Aggregate
mode has this same gap; multiagent inherits it unchanged.

Flash clocks are wall-clock and run from `AgentStatus.updated_at`, so a session
that was waiting when the lid closed renders steady on wake. That is correct —
it really has been waiting a long time, and its cache is cold.

## How long a slot stays lit

A slot is held while its status is in the set `collector.multiagent_statuses`
builds. That is deliberately *not* `snapshot.statuses`, which is the aggregate
indicator's set: there, one LED has to answer one question, so a single active
session demotes every finished one out of sight. On a strip both fit, and green —
"finished, output unread" — is most useful exactly while other sessions are still
running. So finished sessions stay on the strip for their full window whatever
else is happening:

| Mode | Visible for | Constant |
|---|---|---|
| `COMPLETED` | 20 min | `COMPLETED_VISIBLE_SECONDS` |
| `IDLE_READY` | 0 s — effectively never shown | `IDLE_VISIBLE_SECONDS` |
| everything else | 60 min | `stale_after_seconds` |

`IDLE_READY` is in the state table for completeness and to keep the eviction
ordering total, but with `IDLE_VISIBLE_SECONDS = 0.0` an idle status is stale by
the next collection. In practice the strip shows three colours, not four.

## When there are more sessions than LEDs

Importance rank, 1 being most important:

| Rank | Modes |
|---|---|
| 1 | `BLOCKED_ERROR`, `WAITING_FOR_INPUT` |
| 2 | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` |
| 3 | `COMPLETED` |
| 4 | `IDLE_READY` |
| — | `UNKNOWN` — holds no slot, never displayed |

A newcomer with no free slot evicts the **strictly** least important occupant,
ties going to the oldest state entry. A newcomer that outranks nobody is not
displayed at all — so on a strip full of red, a newly working session shows
nothing until something frees up.

## Checking what it is doing

```sh
# Which sessions are live and what the strip should look like
sidepulse agent-monitor leds --once --dry-run --display multiagent

# Hook configuration actually on disk
sidepulse agent-monitor doctor

# What the device is being driven with right now
cat /Volumes/SidePulse/LEDS.LED
```

A live strip with four sessions — three waiting on input, one working, all four
changed within the last five minutes. At default brightness there is no
`brightness` directive at all; the intensity you see is the bands':

```text
0:#3D0000 1:#000000 2:#000000 3:#002A80 4:#3D0000 5:#000000 6:#000000 7:#3D0000
0:#FF0000 900ms pulse; 4:#FF0000 900ms pulse; 7:#FF0000 900ms pulse
repeat 328
```

Line 1 is every slot's settled colour. Line 2 pulses only the recently-changed
slots up to their band's peak and back; `pulse` returns to the line's start
colour, so each cycle is a blink up from the baseline and unmentioned LEDs hold
rock steady. Slot 3 is working and so never appears on line 2 at all, however
recently it changed.

Turn the slider down and a `brightness N` line appears above line 1, scaling all
three bands together.

`repeat` is `ceil(longest_remaining_flash / 917ms)` — 917 ms being the measured
blink cycle for a `900ms pulse`, not a value derived from the pulse duration. It
counts down as the flash window closes, which is why the write-dedupe key
excludes it; keying on the program text would rewrite `LEDS.LED` every 15 s
forever.

Because the count is finite *and* line 1 is the baseline, a dead app leaves a
device holding the settled appearance — not one blinking indefinitely, and not
one frozen on the bright pre-settle colour either. The five-minute write is
therefore visually a no-op: by the time the host notices the boundary the device
already looks right, and the write only removes line 2.

Two `LEDS.LED` parser constraints worth knowing if you edit the renderer, since
`validate_led_text` checks only size and cannot catch either:

- `repeat 0` is rejected (`bad-repeat`) and `repeat 65536` overflows, so the
  count is clamped to `[1, 65535]`. The floor is load-bearing.
- `0:off` is rejected (`bad-index`) where `0:#000000` is fine, so empty slots
  carry an explicit black.
