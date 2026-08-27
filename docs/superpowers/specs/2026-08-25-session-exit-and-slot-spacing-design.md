# Session exit clears its slot, and new slots spread out

Date: 2026-08-25
Status: implemented

## Status

Five commits landed on `my-customizations`. The exit filter is `8436a02`. The
spacing rule is `8494550`. The session re-key of the feed is `fedefb4`, together
with the review-round fixes listed under commit 3. The permission-mirror drop is
`460ed8b`. The second review round is `e0c4876`, which reworks that drop rather
than adding to it. Each commit carries its own tests and the doc changes its code
invalidated.

The suite passes at 398 tests. `test_hook_event_server_receives_socket_message`
fails when the sandbox denies a Unix socket `bind`, and it fails on a clean tree
the same way.

The spacing rule is checked on hardware. A restarted app put two live sessions on
slots 0 and 7 of a mounted SidePulse Pro, where the uniform draw had put three
sessions on 5, 6 and 7.

The branch ran live against real sessions, and the strip behaved as designed. That
is a whole-branch observation, not a check of any one change.

**Known gaps:** the exit path is unchecked as its own case. Nobody has watched a
slot go dark on the strip within a second of leaving a session. The feed tests
cover the filter, and `sidepulse agent-monitor leds --once --dry-run --display
multiagent` prints what the strip would hold.

**Known gaps:** the session re-key is unchecked as its own case. The falsifier that
found the movement drives `SlotAllocator` directly, and the fix is read back the
same way.

**Known gaps:** the permission-mirror drop is unchecked on hardware. The evidence
is a replay of 3588 recorded hook events through `LiveAgentMonitor` and the suite.

**Known gaps:** no test covers a mirror that arrives after
`PERMISSION_NOTIFICATION_MIRROR_SECONDS`. The 60 second bound is checked against
272 measured pairs, whose worst lag was 12 seconds.

## Summary

Four changes to the multiagent strip.

A session that ends gives up its LED at once. Today it holds the done band for 20
minutes. `mode_for_event` maps `SessionEnd` to `COMPLETED`, and
`multiagent_statuses` keeps a completed session for `COMPLETED_VISIBLE_SECONDS`.

A new session takes the free slot whose nearest occupied slot is farthest away.
Today `SlotAllocator._free_slot` picks a free slot at random, so occupied slots
cluster.

A session keeps one index while a sub-agent outranks it. Today the winner of the
coalesce reaches the allocator under its own `agent_id`, so a Task-tool child moves
its parent's LED.

A permission prompt raised inside a sub-agent stops holding the slot red once it is
answered. Today Claude Code's duplicate of that prompt lands on the parent's key,
where nothing expires it.

The four changes share no state and touch different functions. They land as five
commits: one each, and one review round on the last.

## Goals

- An LED goes dark when you exit the session behind it.
- Occupied LEDs spread across the strip while the strip has room.
- A session that holds a slot holds one index, whichever of its statuses wins.
- A slot shows red while a prompt waits for you, and stops when you answer it.
- The aggregate indicator keeps the behaviour it has today.
- Add no new timer and no new `AgentMode`.

The first three changes add no stored state either. The permission-mirror drop
adds one map, `permission_request_keys_by_session`, held per monitor. It is not
persisted, and its entries expire after
`PERMISSION_NOTIFICATION_MIRROR_SECONDS`.

## Non-goals

- Changing the 20-minute window for a session that stops without a `SessionEnd`
  event. A crash and a killed terminal still rely on that window.
- Re-spacing the strip when a session in the middle leaves.
- Changing eviction, or which session outranks which.
- Reading the `reason` field of a `SessionEnd` payload.
- Enabling multiagent on the Dot.
- Showing a sub-agent's prompt on a slot of its own. A child borrows its parent's.

## An ended session leaves the strip

`multiagent_statuses` removes every status that belongs to an ended session.

The function collects the ended-session keys first. It reads `snapshot.statuses`
and the whole of `snapshot.stale_statuses`. It keeps the statuses whose
`event_name` is `SessionEnd`, and it takes the key that `session_coalesce_key`
computes for each one. A key of `None` does not enter the set.

The filter then removes a status on either of two conditions. The status carries
an `event_name` of `SessionEnd`, or its key is in the ended set. It runs on the
combined display list. `multiagent_statuses` builds that list from
`snapshot.statuses` and from the stale completed statuses it re-admits. An ended
session sits in either half, depending on whether another session is active. The
rest coalesces, as it does today.

The allocator needs no change. `SlotAllocator.update` already frees a slot whose
session leaves the incoming set.

### The filter keys on the session, not on the status

A filter that tests the `event_name` of each status fails. It fails on both sides
of the coalesce step, for the same reason.

`coalesce_statuses_by_key` keeps one status per session.
`status_coalesce_sort_key` ranks by priority first and by a sub-agent penalty
second. A sub-agent on `WAITING_FOR_INPUT` therefore outranks its parent's
`COMPLETED` status.

- Filter before the coalesce. The parent's `SessionEnd` status goes. The
  sub-agent's status stays, and the slot stays lit.
- Filter after the coalesce. The winner is the sub-agent's status. Its
  `event_name` is not `SessionEnd`, so the filter matches nothing.

A session-keyed filter removes the parent and its children together.

### The key set is wider than the list the filter runs on

The keys come from every status the snapshot holds. The filter runs on the
display list alone. The two sets differ, and the difference is what keeps the slot
dark.

A `SessionEnd` status reaches the display list only inside
`COMPLETED_VISIBLE_SECONDS`, which is 20 minutes. A sub-agent status keyed to the
same session stays displayable for `stale_after_seconds`, which is 60 minutes.
Collect the keys from the display list, and the filter stops firing 20 minutes
after the exit. The child's status returns to the feed.

That outcome is worse than today's: dark for 20 minutes, then the alert band for
40 minutes, for a session that no longer exists. The index also moves, because
`SlotAllocator._displayable` keys on `agent_id` rather than on the session.

The invariant to hold: an ended session stays out of the feed for as long as any
of its statuses could hold a slot. `snapshot.stale_statuses` carries the
`SessionEnd` status for the life of the process. `LiveAgentMonitor` never prunes
`statuses_by_key`, so nothing ages that status out.

**Rejected:** store the ended session ids. The wider key set needs no new state,
and a stored set would have to be pruned when a session id comes back.

### A status with no session key gives up only its own slot

The filter removes a status on either of two conditions. The status carries an
`event_name` of `SessionEnd`, or its session key is in the ended set. A `None` key
never enters that set.

`session_coalesce_key` returns `None` for a status that carries no `session_id`.
`coalesce_statuses_by_key` passes such a status through untouched, so it holds a
slot of its own.

A `None` key inside the ended set would therefore remove every session-id-less
status on the strip. One `SessionEnd` with no `session_id` would darken an
unrelated working session. The two conditions above keep that exit to its own
slot.

Grok sends `SessionEnd` (`providers.GROK_EVENTS`). Whether a real Grok payload
can arrive with no `session_id` is unverified, because the retained `grok.jsonl`
holds no `SessionEnd` events. The guard costs one condition either way.

### A sub-agent never ends a session

Claude Code sends `SubagentStop` for a Task-tool child, never `SessionEnd`. All
22 `SessionEnd` events in the retained logs carry a session id and no agent id.
A child that finishes cannot darken its parent's slot.

**Known gaps:** a provider that sent `SessionEnd` for a sub-agent would darken
the parent's slot. No provider does this today.

### `/clear` also clears the slot

`/clear` sends `SessionEnd` with `reason` set to `clear`. The filter removes that
session's slot too.

The slot then stays dark until you submit the next prompt. `SessionStart` maps to
`IDLE_READY`, and `IDLE_VISIBLE_SECONDS` is 0, so an idle session holds no slot.
For those few seconds the strip under-reports a window you are working in.

`/clear` also starts a new session id, so the cleared window cannot reclaim its
old slot. Today that slot holds the done band for 20 minutes for a session id
nothing will report again.

**Rejected:** read `reason`, and keep the slot when the value is `clear`. The done
band means "finished, output unread". `/clear` deletes the transcript, so the
output is gone rather than unread. The narrower rule also needs a Claude-specific
payload field, and Codex and Grok send no such field.

### The aggregate indicator does not change

`snapshot.statuses` and `snapshot.aggregate` keep the mapping from `SessionEnd`
to `COMPLETED`. Only `multiagent_statuses` filters. The menu-bar icon, the Dot,
and aggregate mode behave as they do today.

**Rejected:** exempt `SessionEnd` inside `should_ignore_status_transition`. That
function drops a status when the previous status was `WAITING_FOR_INPUT` from a
`PermissionRequest` and permissions are still pending. A `SessionEnd` matches
every one of those conditions, so the exemption looked necessary. It is not.
`track_pending_permissions` empties the pending set on `SessionEnd`, and both
callers call it before the ignore check. A session you abandon at a permission
prompt already records its `SessionEnd`.

`test_session_end_is_not_swallowed_by_a_pending_permission_prompt` guards that
call order in `AgentMonitor`. Move `track_pending_permissions` after the ignore
check and the test fails. It does not reach `LiveAgentMonitor.ingest_record`,
which is the path the strip runs on, so this design adds a test there.

### What you see

| What you do | Today | After |
|---|---|---|
| Open a session, then exit | done band for 20 minutes | dark at once |
| Finish work, then exit | done band for 20 minutes | dark at once |
| Run `/clear` | done band for 20 minutes | dark until the next prompt |
| Exit while a sub-agent waits on a prompt | alert band stays | dark at once |
| Exit with a permission prompt unanswered | done band for 20 minutes | dark at once |
| Kill the terminal | done band for 20 minutes | done band for 20 minutes |

"Dark at once" means dark within a second. The hook sends `SessionEnd` over the
Unix socket, and the LED write follows that event. It does not wait for the 15
second timer.

Two states defer the write to that timer anyway. `StatusBarController.sync_leds`
returns without writing while an LED animation window is open, and again while a
previous write is in flight. Neither state queues the dropped write. The slot then
goes dark on the next refresh instead, up to 15 seconds later.
`sync_virtual_status_device` runs before both checks, so the Screen Bar keeps the
one-second behaviour and only a mounted device waits.

Codex reports no exit at all. `providers.CODEX_EVENTS` has no `SessionEnd`, and
`install` writes Codex hooks from exactly that tuple. Every row of the table above
holds for Claude and for Grok. A Codex session gets the "Today" column.

**Known gaps:** exit a window the moment an agent finishes, and you lose the done
band that says the output is unread. The design accepts this. You closed the
window, so you cannot read that output any more.

## A sub-agent does not move its parent's LED

`multiagent_statuses` hands each surviving status to the allocator under its
session's key. `session_keyed` replaces the `agent_id` of a status that carries a
`session_id` with the key `HookEvent.status_key` builds for the parent. A status
with no `session_id` keeps its own `agent_id`, because it has no session to key on.

The mode still comes from the winner of the coalesce. A child stopped on a
permission prompt still turns the parent's slot red.

**Previously:** the winner reached the allocator under its own `agent_id`.
`coalesce_statuses_by_key` returns whichever member of the session ranks highest,
and `status_coalesce_sort_key` lets a child on `WAITING_FOR_INPUT` beat a parent on
`WORKING`. `SlotAllocator._displayable` keys its map on `agent_id`, so the
allocator read that child as one session leaving and another arriving. One
Task-tool child therefore moved the LED twice: once when it took the rank, and once
when it handed the rank back. On an 8-slot strip seeded with `random.Random(1234)`
the index went 7, then 1, then 0.

**Rejected:** teach `SlotAllocator._displayable` to key on the session. The
module docstring of `led_slots.py` says a slot is a top-level session and that
`collector.multiagent_statuses` is what enforces it, so the session concept belongs
upstream of the allocator.

The movement predates both other changes, because `multiagent_statuses` already
coalesced by session. The guarantee is what is new: the exit filter's commit added
the sentence "A session that already holds a slot never moves" to `docs/usage.md`,
and that sentence was false for any session running a Task-tool child.

## An answered sub-agent prompt stops holding the slot red

`notification_mirrors_another_agents_permission` drops the duplicate that Claude
Code fires for a permission prompt raised inside a sub-agent.

Claude Code reports one prompt as two hooks. `PermissionRequest` carries the
`agent_id` of whoever raised it, so a prompt raised inside a Task lands on the
child's key. The `Notification` that follows it carries no `agent_id`, so it lands
on the parent's key. Answering the prompt clears the child's copy through the
`PostToolUse` that follows. Nothing clears the parent's, because a parent blocked
inside the Task tool emits no hook of its own until the Task returns.

That leaves the duplicate as the parent's newest status.
`status_coalesce_sort_key` sorts on priority before recency, so a stale
`WAITING_FOR_INPUT` beats every child's fresh `WORKING`. The slot holds the alert
band for the rest of the sub-agent's run, which after the five-second flash reads
as steady red.

Dropping the duplicate costs the display nothing. The child's own
`PermissionRequest` already carries the alert. `session_keyed` lends it to the
parent's slot, and its pending-signature lifecycle expires it on the event that
answers it.

**Rejected:** teach `status_coalesce_sort_key` to prefer a newer sibling over an
older session-keyed alert. That breaks the case the alert band exists for. A parent
genuinely waiting on a human, while a background child works, has to stay red.

**Rejected:** catch the duplicate in `track_pending_permissions`. A `Notification`
carries no `tool_input`, so `permission_signature` returns `None`. The duplicate
never enters the pending set that expires the alert.

### The drop reads a timing window, not the `prompt_id`

`track_permission_request_keys` remembers which keys raised a prompt in each
session, and when. The drop fires when some key other than the session's own raised
a prompt within `PERMISSION_NOTIFICATION_MIRROR_SECONDS`, which is 60.

Both payloads carry a `prompt_id`, which looks like the exact pairing this reaches
for by timing. That field identifies the turn, not the prompt. In the retained logs
64 distinct `prompt_id` values cover 424 `PermissionRequest` events, and one value
covers 36 requests with 31 different commands. Pairing on it folds a turn's prompts
into one.

The measured lag from request to duplicate is 6 seconds at the median and 12
seconds at the worst, over 272 pairs. The bound has five times that headroom.

**Known gaps:** a duplicate delayed past 60 seconds is not dropped, and the slot
holds red until the parent's next hook. That is the behaviour the drop replaced, so
the failure costs nothing new.

### Every key that raised a prompt stays readable

`track_permission_request_keys` holds a map of key to timestamp per session, not one
key per session.

A parent that dispatches a Task and makes a tool call of its own raises two prompts
seconds apart. Remembering one key per session loses the child's the moment the
parent's overwrites it. The child's duplicate then arrives with no non-session key
to pair against, is not dropped, and pins the slot red — the same symptom, reached
by a second route.

Both duplicates are dropped when two prompts are open. They are indistinguishable,
and the parent's own `PermissionRequest` is session-keyed, so the slot stays red
from the request itself while the human is still waiting.

**Previously:** one `(key, timestamp)` per session, last write wins. The overwrite
does not appear in the retained logs, across 273 duplicates. Nine cross-agent
overlapping requests do appear, across two sessions.

### The window is what bounds the map

Each write to `track_permission_request_keys` removes every entry older than
`PERMISSION_NOTIFICATION_MIRROR_SECONDS`, in every session. A session left with no
entries leaves the map.

No duplicate can pair with an expired entry, so an expired entry has no reader. The
map therefore holds the sessions raising prompts now. The status-bar app runs one
`LiveAgentMonitor` for days, and a map keyed by every session ever seen would grow
for that whole time.

Replaying 3588 recorded hook events holds the map at 4 sessions at its widest.

## New slots spread across the strip

`_free_slot` picks the free slot whose distance to the nearest occupied slot is
largest.

1. When no slot is occupied, pick one slot uniformly at random from all slots.
2. Otherwise measure, for each free slot, the distance to the nearest occupied
   slot.
3. Keep the free slots that tie for the largest distance.
4. Pick one uniformly at random from those.

Distance is the absolute difference of two slot indexes. The strip is a line, so
distance does not wrap from slot 7 to slot 0.

Four sessions entering an empty 8-slot strip each land in the largest remaining
gap. The rules allow the order 3, then 7, then 0, then 5.

**Previously:** `_free_slot` picked uniformly at random from the free slots. A
position is arbitrary but sticky, so repetition teaches you which LED is yours.
Uniform choice keeps that property, and it still clusters. A live strip put three
sessions on slots 5, 6 and 7.

### A session that holds a slot never moves

Spacing applies when the allocator assigns a slot. The strip does not re-space
itself when a session in the middle leaves.

Re-spacing would move live sessions. That destroys the property that makes a
position worth learning. `session_keyed` holds the same property from the other
side, by keeping one key per session while the session is displayable.

**Known gaps:** sessions that start and stop repeatedly can still cluster the
strip. The design accepts this.

**Known gaps:** on a two-session strip, the next newcomer takes the index the
session that just left was holding, every time rather than one time in seven. The
second session sat at the free slot farthest from the first, so once it leaves, its
index is still the only widest gap from the one remaining occupant. The uniform draw
spread that across the free slots. To get the spread back, a slot freed in the same
`update` call has to lose the tie-break rather than win it, which this design does
not do.

### Eviction does not change

`_evictable_slot` returns the slot of the weakest occupant. It chooses no free
slot, so spacing does not apply to it.

### Seeded tests keep the positions they assert

Rule 1 issues the same random draw as the current code, over the same list. A test
that puts one session into an empty strip gets the slot it gets today.
`test_a_new_session_takes_a_random_free_slot` asserts slot 7 and still passes.

No existing test asserts an index for two or more sessions on a partly-empty
strip. `test_a_two_led_strip_allocates_two_slots` and
`test_a_completed_session_renders_green_beside_a_working_one` each assert a sorted
list of colours, and the first fills a two-slot strip, where no free-slot choice
exists. The eviction tests and `test_the_freed_slot_rejoins_the_pool` read the
index back through `slot_for_key`. Do not pin a fresh index in any of them: an
index assertion is weaker than the colour assertion it would replace.

## Tests

Write each test before the code it covers.

For the exit filter, in `MultiagentStatusFeedTests` in `tests/test_led_slots.py`:

- A session that sends `SessionEnd` holds no slot.
- A session that sends `SessionEnd` while its sub-agent waits on a prompt holds no
  slot. Read the feed 21 minutes after the exit, not one minute after. This covers
  the sub-agent trap and the wider key set together. Every scenario read one
  minute after the exit passes with the keys collected from the display list.
- A `SessionEnd` whose `reason` is `clear` also holds no slot.
- A `SessionEnd` that carries no `session_id` leaves an unrelated session's slot
  lit. This covers the `None` key.
- A completed session with no `SessionEnd` stays visible for 20 minutes. This
  keeps the backstop.

For the scope boundary and the call order, in `tests/test_sidepulse.py`:

- An ended session still reaches `snapshot.aggregate` as `COMPLETED`. This keeps
  the scope boundary.
- `LiveAgentMonitor.ingest_record` keeps the `SessionEnd` that follows a
  `PermissionRequest`. Feed `PreToolUse`, then `PermissionRequest`, then
  `SessionEnd` for one session, and check that the surviving status is `SessionEnd`
  on mode `COMPLETED`. This design's payoff rides on that push path, and the
  existing call-order test covers `AgentMonitor` only. Check that
  `multiagent_statuses` returns nothing for that snapshot as well. The status
  assertions alone pass with either half of the filter deleted.

For the spacing rule, in `tests/test_led_slots.py`:

- Two sessions on an empty 8-slot strip land at least 4 slots apart. Slot 3 and
  slot 4 are the worst first positions, and each leaves a farthest free slot at
  distance 4.
- A newcomer takes the largest gap when one slot is occupied.
- A newcomer picks at random among free slots that tie for the largest distance.
- A session that already holds a slot keeps its index when another session
  arrives.

For the session key, in `MultiagentStatusFeedTests` in `tests/test_led_slots.py`:

- A winning sub-agent status reaches the feed under its parent's key, and keeps its
  own mode.
- A session keeps one index across two rank crossings. Feed the parent alone, then
  the parent with a child on `WAITING_FOR_INPUT`, then the parent with that child on
  `WORKING`, and read the index back through `slot_for_key` after each. Assert that
  the three indexes are one index rather than a seeded value: the content of the
  test is that the index does not change.

For the permission-mirror drop, in `AgentMonitorTests` in `tests/test_sidepulse.py`:

- A session whose sub-agent prompt is answered reads as the working band, on
  `LiveAgentMonitor.ingest_record`. This is the push path the strip runs on.
- The same scenario reads as the working band through `AgentMonitor`. This is the
  scan path the CLI reads, and it reaches the ignore check by its own route.
- A parent's own prompt reads as the alert band while a child works.
- A child's prompt and a parent's prompt, both answered before the child's
  duplicate arrives, read as the working band. This is the overwrite case. Assert
  the band, not the map: the symptom is a red slot.
- A parent's unanswered prompt reads as the alert band while a child's prompt is
  open. This holds the guarantee that the drop costs the display nothing.
- A parent's prompt raised 5 minutes after a child's answered one reads as the
  alert band. This holds the per-key expiry. Without it a stale child entry makes
  every later duplicate droppable.
- Three sessions that raise prompts 10 minutes apart leave one entry in
  `permission_request_keys_by_session`. This holds the bound on the map.
- `notification_mirrors_another_agents_permission` takes the mode as an argument.
  Call it with `WAITING_FOR_INPUT` and with `COMPLETED` for one duplicate, and
  check that only the first drops.

## Commits

Five commits, in this order. The code and the tests of each one revert without the
others, except commit 5, which reworks commit 4. This spec does not revert
per-commit: `8436a02` carries the whole spec rewrite, including the sections that
describe the spacing rule in `8494550`. Each commit carries the doc changes its own
code invalidates.

1. The `SessionEnd` filter in `collector.multiagent_statuses`, with its tests.
   Two doc claims go stale. The "How long a slot stays lit" section of
   `docs/usage.md` says finished sessions stay on the strip for their full window
   whatever else is happening, which after this commit holds only for a session
   that stops without a `SessionEnd` event. `README.md` says `Completed` remains
   visible for 20 minutes.
2. The spacing rule in `SlotAllocator._free_slot`, with its tests. Two doc claims
   go stale. The "Slot positions are sticky per process, not persistent" section
   of `docs/usage.md` says positions are assigned at random, which after this
   commit describes the first position alone. The "Random rather than
   left-packed" comment on `_free_slot` is the sentence the new rule replaces.
3. `session_keyed` in `collector.multiagent_statuses`, with its tests. One doc
   claim becomes true rather than stale: `docs/usage.md` says a session that
   already holds a slot never moves. This commit also closes the review round on
   the first two commits — the provider condition and the deferred write in
   `docs/usage.md` and `README.md`, the strip assertion on the push-path test, the
   `getattr` default in `ended_session_keys`, and a test docstring that read a
   distance as an index.
4. `notification_mirrors_another_agents_permission` and
   `track_permission_request_keys`, with their tests. This commit depends on
   commit 3: the drop is safe because `session_keyed` lends a child's alert to the
   parent's slot.
5. The second review round on commit 4. Three findings are taken: the per-session
   overwrite, the unbounded map, and the recomputed mode. The map becomes a map of
   maps, the expiry runs across every session, and the mode becomes an argument.
   Three findings are rejected in this spec: the `prompt_id` pairing above, folding
   the map into `StatusMetadata`, and folding the drop into
   `should_ignore_status_transition`. One `docs/usage.md` claim goes stale: it says
   the drop fires for a duplicate whose request was seen under a different key,
   which after this commit reads as any other key with a live request.

**Rejected:** fold the map into `StatusMetadata`. That dataclass is frozen, holds
display metadata, and `metadata_by_status` keys it by status key rather than by
session. Reusing it needs a different key, a different lifetime, and an unfrozen
field.

**Rejected:** fold the drop into `should_ignore_status_transition`. That function
is a predicate over a previous status, a current status and a signature set. The
drop asks about the record instead, so folding it in threads the record and the
per-session map through a function that takes neither.
