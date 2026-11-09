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
| Red | `#B00000` | `#FF1000` | that session wants something from you | `BLOCKED_ERROR`, `WAITING_FOR_INPUT` |
| Blue | `#0000E6` | — never flashes | working | `TOOL_RUNNING`, `WORKING`, `LONG_TASK_PROGRESS` |
| Chartreuse | `#58B000` | `#80FF00` | finished, output unread | `COMPLETED`, `IDLE_READY` |
| Off | `#000000` | — | empty slot | — |

Red and chartreuse **blink for the first five minutes** after a session enters the
band, pulsing from off up to the peak and back to off about once every 1.8 s,
then hold settled. Settled is
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

Red and chartreuse both peak at full, and after five minutes both settle
*below* a working slot's steady 90% — so once things are quiet, hue is what
tells them apart, not brightness.

Intensity lives in those hex values, not in the `brightness` directive, which is
program-global and so cannot vary per slot. The directive carries your brightness
slider on top, as a master gain over all three bands.

The hex values were chosen by eye on a mounted device, one band at a time, which
is why the tiers are uneven and the secondary channels look arbitrary. Numeric
intensity is not perceived intensity: blue carries no green at all and is turned
up to 90% to hold its own beside red, and the finished band is chartreuse rather
than green. The code has them in one place, the
`BAND_ALERT` / `BAND_WORKING` / `BAND_DONE` table in `led_slots.py`; this file
restates them in the table above, in the worked example further down and in two
effective-intensity figures, so a retune moves those too.

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
scales all three bands together. Red and chartreuse settle at 69% and blue
holds 90%, so at a low slider setting the settled colours get faint — at 25%,
`#B00000` is an effective red of about 44/255.

That is accepted rather than floored. Dimming the strip to almost nothing is a
reasonable thing to want from a brightness slider, and a floor would surprise
whoever asked for it. If settled slots are too faint for you, raise the slider or
raise the `SlotBand.steady` values in `led_slots.py`.

If you used this mode before the bands landed, expect the strip to look
different. A settled alert slot is now an effective red of 176/255 where the old
flat amber ran at 128. The slider is a second, independent change — it was hidden in this mode and
ignored by the renderer, so whatever it was left at (255 unless you moved it
while the device was on another display kind) now applies for the first time.
The submenu shows the current percentage above the slider, so check it before
reaching for it.

### Slot positions are sticky per process, not persistent

A session keeps its slot for as long as it stays visible, so you learn "mine is
the third one" by repetition. The first session of a run lands at random. Every
newcomer after it takes the free slot farthest from the nearest occupied one, and
draws at random between slots that tie, so sessions spread across the strip
instead of clustering at one end. A session that already holds a slot never moves,
not even when the session beside it exits. Allocator state lives only in memory —
restart the app and everything reshuffles. Slots
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

The surviving status is handed to the allocator under the parent's key, not the
winning child's. That is what keeps the slot still: the allocator keys its map on
`agent_id`, so a child that outranks its parent and then hands the rank back would
otherwise read as one session leaving and another arriving, and the LED would move
twice per sub-agent.

A permission prompt raised inside a child is the one case where the fold is not
enough on its own. Claude Code reports such a prompt twice: as a
`PermissionRequest` carrying the child's `agent_id`, and a few seconds later as a
`Notification` carrying no `agent_id` at all, which therefore lands on the parent's
key. Answering the prompt clears the child's copy and nothing clears the parent's,
because a parent blocked inside the Task tool emits no hook of its own until the
Task returns — so the parent's newest status stays `WAITING_FOR_INPUT`, outranks
every child's `WORKING` on the priority-first sort, and holds the slot red for the
rest of the sub-agent's run. `collector.notification_mirrors_another_agents_permission`
drops that duplicate. It costs nothing, because the child's own request already
raises the alert and expires it on the right event, and it fires only while some
other key in that session has raised a prompt within the last minute — so a prompt
raised when no child has one open, or a prompt whose `PermissionRequest` hook never
arrived, still turns the slot red.

A parent that dispatches a Task and makes a tool call of its own raises two prompts
seconds apart, and their mirrors are indistinguishable, so both are dropped. The
parent's own prompt still shows red: its `PermissionRequest` is session-keyed and
carries the alert by itself. What the mirror adds is nothing the display needs.

`collector.track_permission_request_keys` is what the drop reads, and it remembers
every key that raised a prompt rather than the newest one — a parent's later prompt
must not erase the child's before the child's mirror arrives. Entries expire after
`collector.PERMISSION_NOTIFICATION_MIRROR_SECONDS`, which is also what keeps the map
sized to the sessions asking now rather than to every session a days-long process
has seen. The pairing is by timing and not by the `prompt_id` both payloads carry:
that field identifies the turn, and one turn can raise dozens of prompts.

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
that was waiting when the lid closed renders settled on wake. That is correct —
it really has been waiting a long time, and its cache is cold.

## How long a slot stays lit

A slot is held while its status is in the set `collector.multiagent_statuses`
builds. That is deliberately *not* `snapshot.statuses`, which is the aggregate
indicator's set: there, one LED has to answer one question, so a single active
session demotes every finished one out of sight. On a strip both fit, and the
finished band —
"finished, output unread" — is most useful exactly while other sessions are still
running. So a finished session stays on the strip for its full window however
busy the rest of the machine is, and only its own exit cuts that short:

| Mode | Visible for | Constant |
|---|---|---|
| `COMPLETED` | 20 min | `COMPLETED_VISIBLE_SECONDS` |
| `IDLE_READY` | 0 s — effectively never shown | `IDLE_VISIBLE_SECONDS` |
| everything else | 60 min | `stale_after_seconds` |

`IDLE_READY` is in the state table for completeness and to keep the eviction
ordering total, but with `IDLE_VISIBLE_SECONDS = 0.0` an idle status is stale by
the next collection. In practice the strip shows three bands, not four.

### Exiting the session beats every row of that table

A session that reports `SessionEnd` gives its slot up within a second, along with
every sub-agent it was running. The write follows the socket event rather than the
15 s refresh. Two states defer it to that refresh anyway: an LED animation still
running (a battery preview on a power change, a lid animation), and a previous LED
write still in flight. `StatusBarController.sync_leds` drops the write in both, and
nothing replays it, so the slot can stay lit for up to another 15 seconds.
`sync_virtual_status_device` runs before either check, so only a mounted device
waits and the Screen Bar goes dark on time.

Reporting an exit at all needs a provider whose hook event set carries
`SessionEnd`, which today means Claude and Grok. `providers.CODEX_EVENTS` has no
such event, and `install` writes Codex hooks from exactly that tuple, so a Codex
session never says it exited and its slot keeps the window in the table above.

The windows above are also what any session gets when it stops *without* saying
so — a crash, a killed terminal, a `kill -9`.

`/clear` counts as an exit. It also starts a new session id, so the cleared window
takes a fresh slot at your next prompt rather than reclaiming the old one, and the
strip shows nothing for it in between.

Only this feed drops an ended session. The menu-bar icon and the aggregate LED keep
the `COMPLETED` mapping, so an exit still reads as Done there. That is the
aggregate's own window rather than the strip's: up to 20 minutes, and only while
nothing else is running, because one LED answering for the whole machine demotes
every finished session as soon as anything is active.

The cost is a real one: exit a window the moment an agent finishes and you lose
the chartreuse LED that says its output is unread. `collector.multiagent_statuses`
takes that trade because you closed the window, so there is nothing left to go
back and read.

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
0:#000000 1:#000000 2:#000000 3:#0000E6 4:#000000 5:#000000 6:#000000 7:#000000
0:#FF1000 1800ms pulse; 4:#FF1000 1800ms pulse; 7:#FF1000 1800ms pulse
repeat 166
0:#B00000 4:#B00000 7:#B00000
```

Line 1 is every slot's baseline colour, with the recently-changed slots at off.
Line 2 pulses only those slots up to their band's peak and back; `pulse` returns
to the line's start colour, so each cycle is a blink from off to full and
unmentioned LEDs hold rock steady. The off has to be on line 1 because `repeat`
loops from line 1: a settled colour there would flicker on for one frame between
every blink. The last line sets the flashing slots to their settled colour once
`repeat` runs out. Slot 3 is working and so never appears on line 2 at all,
however recently it changed.

Turn the slider down and a `brightness N` line appears above line 1, scaling all
three bands together.

`repeat` is `ceil(longest_remaining_flash / 1817ms)` — 1817 ms being the
measured blink cycle for an `1800ms pulse`, not a value derived from the pulse
duration. It was measured against the firmware parser and has not yet been
confirmed on a physical strip. It
counts down as the flash window closes, which is why the write-dedupe key
excludes it; keying on the program text would rewrite `LEDS.LED` every 15 s
forever.

Because the count is finite *and* the line after `repeat` settles the flashing
slots, a dead app leaves a device holding the settled appearance — not one
blinking indefinitely, and not one frozen at the off point of a blink either. On the MacBook built-in SD
reader that holds for about three minutes and then the strip goes dark, because a
dead app also stops the once-a-minute `keepalive` touch and the reader powers the
device off after three minutes of inactivity. That dark is the reader's doing,
not the program's. For the last slot to settle,
that five-minute write is visually a no-op: the count has already run out and the
device is holding the settled look before the host notices the boundary. For an
earlier one it is not — `repeat` is sized to the longest remaining flash, so a
slot that crosses its own five-minute mark keeps blinking until the next refresh
drops it from line 2. Either way the settling slot lands on the colour the old
program's last line would have given it.

Two `LEDS.LED` parser constraints worth knowing if you edit the renderer, since
`validate_led_text` checks only size and cannot catch either:

- `repeat 0` is rejected (`bad-repeat`) and `repeat 65536` overflows, so the
  count is clamped to `[1, 65535]`. The floor is load-bearing.
- `0:off` is rejected (`bad-index`) where `0:#000000` is fine, so empty slots
  carry an explicit black.
