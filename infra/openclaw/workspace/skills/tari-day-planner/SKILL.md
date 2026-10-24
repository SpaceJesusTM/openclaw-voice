---
name: tari-day-planner
description: "Manage the unified Calendar/day planner and persistent local checklist through bin/day-planner."
user-invocable: false
---

# Day planner

Use this skill for scheduling or planner/checklist requests. Read the Calendar
skill too. Ordinary scheduling creates one Calendar event; no need to ask
Calendar versus planner. Use `./bin/day-planner` for planner operations.

All Calendar events appear live on their scheduled day, including all-day,
shared and declined entries. Context-only/declined entries do not occupy busy
time. Calendar identity is preserved in `show --json` (`calendar`, `id`).
New `add`, `place` and `commute-add` commands create Calendar events directly,
with private kind metadata, and return a verified `calendar_event` receipt.
`move`, `rename`, `resize`, `remove` find both Calendar and legacy local entries;
they update the same corresponding event. Use the returned Calendar receipt in
CLAWVOICE_RECEIPT, even when the user called it a planner block.
For recurring, all-day, multi-day and clock-change events use Calendar management
with explicit scope instead. Never retry an uncertain write blindly; inspect first.

Existing local blocks, especially Breakfast, are preserved and still editable
locally. Never migrate or delete them automatically or link by matching a title.
If a local block and Calendar event both match, ask for the unique ID. Explicit
`--local-only` creates a new local block when the user requests that exception.
Never edit Markdown by hand. Checklist tasks remain local.

The categorized checklist persists across dates. Each task has a completion flag
and an independent optional status: Priority, Start, or In-progress. The default
public checklist is empty; sections belong to the user.

## Commands

```sh
./bin/day-planner show today
./bin/day-planner show tomorrow --schedule
./bin/day-planner show tomorrow --json
./bin/day-planner free tomorrow --after 12:00 --before 18:00 --min 90m
./bin/day-planner add tomorrow 13:00-14:00 "Project work"
./bin/day-planner place tomorrow 90m "Write draft" --after 14:00
./bin/day-planner move tomorrow "Project work" 16:00-17:00
./bin/day-planner move today b2 --to-day tomorrow
./bin/day-planner resize tomorrow b2 2h
./bin/day-planner rename tomorrow b2 "Review draft"
./bin/day-planner remove tomorrow @16:00
./bin/day-planner commute-add tomorrow to "Team meeting" --duration 45m
./bin/day-planner commute-add tomorrow home "Team meeting" --duration 45m

./bin/day-planner checklist --status priority
./bin/day-planner section-add "Personal"
./bin/day-planner task-add "Personal" "Book appointment" --status priority
./bin/day-planner task-status "Book appointment" in-progress
./bin/day-planner task-complete "Book appointment"
./bin/day-planner task-reopen "Book appointment"
./bin/day-planner task-rename "Book appointment" "Book annual appointment"
./bin/day-planner task-remove "Book annual appointment"
```

Resolve natural language to explicit dates and unambiguous times using
`TARI_TIMEZONE`. A bare `1` is refused; use `13:00` or `1pm`. Resolve a reference
with the id from `show --json` or `checklist --json` when several titles match.
Blocks stay within one day. A commute `to` an event ends at its start; a commute
`home` begins at its end. Use `--keep-end` when resizing an outbound commute.

`task-add` fails when a section does not exist. Pass `--create` only if the user
clearly intends to create that section. Never turn a suggested schedule into
saved blocks or suggested priorities into checklist changes without authorization.

## Calendar checks and overlap confirmation

The helper reads Calendar commitments before adding, placing, moving, resizing,
or creating commute blocks. New timed entries write Calendar using the existing
separate Calendar write profile; legacy local entries remain local.

An overlap returns exit 4 and writes nothing. Explain the specific conflicting
items and obtain the user's confirmation before repeating with
`--allow-overlap`. Never pass that flag preemptively. A confirmation covers that
specific overlap, not unrelated future conflicts.

An unavailable or incomplete Calendar returns exit 3 for a mutation and writes
nothing. Tell the user the check failed. Use `--no-calendar` only after the user
explicitly agrees to proceed without checking Calendar. Read-only views can show
local blocks with a visible Calendar warning; never describe the resulting free
slots as verified availability.

The user's own events count unless the user declined. Shared events count only
when the user's attendee response is accepted. Tentative, unanswered, or unknown
shared invitations and other shared-calendar events remain context and do not
block time unless the user explicitly includes one with `--count`. All-day
events are shown but do not block time. Transparent events are shown without
blocking time.

Only when the user asks about a missing event or explicitly says a shared event
involves them, run `./bin/day-planner excluded DAY`. This is diagnostic output;
do not relay unrelated people's event titles or calendar identities. Use
`--count EVENT_ID` on subsequent commands to include that specific event.

## Responses and state

Relay the helper's planner view in its existing order. After a mutation, give a
short confirmation; show the full view when asked. If it fails, report the failure
without inventing a replacement view. Translate backend errors into a short,
useful explanation; do not paste raw stderr or identifiers into normal replies.

Legacy blocks and checklist state use local Markdown under `planner/`. There is
no local Calendar mirror. New events store their planner/commute kind in private
Calendar extended properties. Commute anchors set initial timing only; they are
not a live dependency graph. The optional `TARI_PLANNER_DIR` override is
for a separately isolated local state directory, including tests.

In ClawVoice, this directory must remain inside the active demo workspace;
symlink escapes are refused. The default planning zone is America/Toronto.
Day boundaries and Calendar instants use IANA conversion, including 23/25-hour
days. Local Markdown blocks cannot distinguish two occurrences of a repeated
clock hour: refuse ambiguous/nonexistent local ranges, clarify with the user,
and choose an unambiguous range. Free/place excludes that clock-change interval.
Calendar events retain exact offset-bearing times; a fold-spanning event may
occupy the repeated hour conservatively for local availability.
