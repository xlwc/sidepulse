# Session exit clears its slot, and new slots spread out

Date: 2026-08-25
Status: accepted, implementing

## Summary

Two independent changes to the multiagent strip.

A session that ends gives up its LED at once. Today it holds a green LED for 20
minutes. `mode_for_event` maps `SessionEnd` to `COMPLETED`, and
`multiagent_statuses` keeps a completed session for `COMPLETED_VISIBLE_SECONDS`.

A new session takes the free slot that sits farthest from every occupied slot.
Today `SlotAllocator._free_slot` picks a free slot at random, so occupied slots
cluster.

The two changes share no state and touch different files. They land as two
commits.

## Goals

- An LED goes dark when you exit the session behind it.
- Occupied LEDs spread across the strip while the strip has room.
- The aggregate indicator keeps the behaviour it has today.
- Add no new timer, no new stored state, and no new `AgentMode`.

## Non-goals

- Changing the 20-minute window for a session that stops without a `SessionEnd`
  event. A crash and a killed terminal still rely on that window.
- Moving a session that already holds a slot.
- Changing eviction, or which session outranks which.
- Reading the `reason` field of a `SessionEnd` payload.
- Enabling multiagent on the Dot.

## An ended session leaves the strip

`multiagent_statuses` removes every status that belongs to an ended session.

The function finds the statuses whose `event_name` is `SessionEnd`. It takes the
key that `session_coalesce_key` computes for each one. It removes every status
that carries one of those keys. Then it coalesces the rest, as it does today.

The filter runs on the combined list. `multiagent_statuses` builds that list from
`snapshot.statuses` and from the stale completed statuses it re-admits. An ended
session sits in either half, depending on whether another session is active.

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

**Rejected:** read `reason`, and keep the slot when the value is `clear`. Green
means "finished, output unread". `/clear` deletes the transcript, so the output is
gone rather than unread. The narrower rule also needs a Claude-specific payload
field, and Codex and Grok send no such field.

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
call order. Move `track_pending_permissions` after the ignore check and the test
fails.

### What you see

| What you do | Today | After |
|---|---|---|
| Open a session, then exit | green for 20 minutes | dark at once |
| Finish work, then exit | green for 20 minutes | dark at once |
| Run `/clear` | green for 20 minutes | dark until the next prompt |
| Exit while a sub-agent waits on a prompt | red stays | dark at once |
| Exit with a permission prompt unanswered | green for 20 minutes | dark at once |
| Kill the terminal | green for 20 minutes | green for 20 minutes |

"Dark at once" means dark within a second. The hook sends `SessionEnd` over the
Unix socket, and the LED write follows that event. It does not wait for the 15
second timer.

**Known gaps:** exit a window the moment an agent finishes, and you lose the
green LED that says the output is unread. The design accepts this. You closed the
window, so you cannot read that output any more.

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
position worth learning.

**Known gaps:** sessions that start and stop repeatedly can still cluster the
strip. The design accepts this.

### Eviction does not change

`_evictable_slot` returns the slot of the weakest occupant. It chooses no free
slot, so spacing does not apply to it.

### Seeded tests keep one-session positions and need new multi-session ones

Rule 1 issues the same random draw as the current code, over the same list. A
test that puts one session into an empty strip gets the slot it gets today. The
tests seeded with `random.Random(1234)` that assert slot 7 all still pass.

A test that allocates two or more sessions and asserts an index needs a new
expected value. `test_a_two_led_strip_allocates_two_slots` and
`test_a_completed_session_renders_green_beside_a_working_one` are two of them.

## Tests

Write each test before the code it covers.

For the exit filter, in `tests/test_sidepulse.py`:

- A session that sends `SessionEnd` holds no slot.
- A session that sends `SessionEnd` while its sub-agent waits on a prompt holds
  no slot. This covers the sub-agent trap.
- A `SessionEnd` whose `reason` is `clear` also holds no slot.
- A completed session with no `SessionEnd` stays visible for 20 minutes. This
  keeps the backstop.
- An ended session still reaches `snapshot.aggregate` as `COMPLETED`. This keeps
  the scope boundary.

For the spacing rule, in `tests/test_led_slots.py`:

- Two sessions on an empty 8-slot strip land at least 4 slots apart. Slot 3 and
  slot 4 are the worst first positions, and each leaves a farthest free slot at
  distance 4.
- A newcomer takes the largest gap when one slot is occupied.
- A newcomer picks at random among free slots that tie for the largest distance.
- A session that already holds a slot keeps its index when another session
  arrives.
- One session on an empty strip lands where the current seeded tests expect.

## Commits

Two commits, in this order. Either one reverts without the other.

1. The `SessionEnd` filter in `collector.multiagent_statuses`, with its tests.
2. The spacing rule in `SlotAllocator._free_slot`, with its tests and the
   updated multi-session expectations.
