# Multiagent LED slots — usage

One LED per agent session instead of one aggregate indicator. This is the
`multiagent` value for `led_display`, added alongside `agent`, `battery` and
`custom`.

Design rationale lives in
[`docs/superpowers/specs/2026-08-24-multiagent-led-slots-design.md`](superpowers/specs/2026-08-24-multiagent-led-slots-design.md).
This file is the operational half: how to turn it on, and what will surprise you.

## What you see

| Colour | Hex | Means | Modes |
|---|---|---|---|
| Amber | `#FF3A00` | that session wants something from you | `BLOCKED_ERROR`, `WAITING_FOR_INPUT` |
| Cyan | `#00E5FF` | working | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` |
| Green | `#00FF66` | finished, output unread | `COMPLETED`, `IDLE_READY` |
| Off | `#000000` | empty slot | — |

A slot **blinks for the first five minutes** after it enters a colour, then holds
steady. Steady is not "stale" — it is "this has been true for a while".

Five minutes is the prompt cache TTL, and that is the whole point of the
threshold rather than a round number. A blinking slot is one whose cache is
probably still warm, so picking it up now is cheap. Once it settles, resuming
pays full price — which is also why the flash deliberately stops instead of
nagging until answered. A slot that blinked forever would be asserting something
no longer true.

Brightness is 128. Everything renders at that; see the gotcha below.

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

### Per-device brightness is ignored in this mode

Unlike the other three display kinds, multiagent always renders at brightness
128 and does not read `brightness_for_device`. Eight LEDs lit at 255 are
glaring. Wiring the setting through is a follow-up, marked at the call site in
`status_bar.multiagent_controller_for_device`. Until then the device submenu
omits its brightness readout and slider in this mode rather than showing controls
that do nothing.

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
child stopped on a permission prompt turns the parent's slot amber.

### The Screen Bar can disagree with the physical strip

`sync_virtual_status_device` renders from the mode rather than reusing the
strip's program, so it has its own allocator, seeded independently. Colours agree,
but nothing else is guaranteed:

- **Positions differ.** Each allocator builds its own `random.Random`, so the same
  session sits at a different index on the Screen Bar than on the strip. This is
  true from first use, not something that drifts.
- **Counts can differ.** `virtual_slot_allocator` is fixed at 8 slots, while a
  physical device's count comes from `led_count_for_target`. Four live sessions
  light four Screen Bar slots and two LEDs on a Dot.

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
displayed at all — so on a strip full of amber, a newly working session shows
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
changed within the last five minutes:

```text
brightness 128
0:#FF3A00 1:#000000 2:#000000 3:#00E5FF 4:#FF3A00 5:#000000 6:#000000 7:#FF3A00
0:#000000 900ms pulse; 3:#000000 900ms pulse; 4:#000000 900ms pulse; 7:#000000 900ms pulse
repeat 328
```

Line 2 is every slot's steady colour. Line 3 dips only the recently-changed
slots to black and back; `pulse` returns to the line's start colour, so each
cycle is a blink against the baseline and unmentioned LEDs hold rock steady.

`repeat` is `ceil(longest_remaining_flash / 917ms)` — 917 ms being the measured
blink cycle for a `900ms pulse`, not a value derived from the pulse duration. It
counts down as the flash window closes, which is why the write-dedupe key
excludes it; keying on the program text would rewrite `LEDS.LED` every 15 s
forever. Because the count is finite, a dead app leaves a device that stops
blinking on its own rather than one blinking indefinitely.

Two `LEDS.LED` parser constraints worth knowing if you edit the renderer, since
`validate_led_text` checks only size and cannot catch either:

- `repeat 0` is rejected (`bad-repeat`) and `repeat 65536` overflows, so the
  count is clamped to `[1, 65535]`. The floor is load-bearing.
- `0:off` is rejected (`bad-index`) where `0:#000000` is fine, so empty slots
  carry an explicit black.
