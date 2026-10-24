---
name: tari-day-planner
description: "Manage Tari's opt-in local day planner, time blocks, commute blocks, and persistent checklist through bin/day-planner."
user-invocable: false
---

# Day planner

Use this skill when the user clearly names their planner or checklist, or continues
an unmistakable planner operation. An ordinary request for planning advice does
not authorize changes. Words such as “urgent”, “schedule”, or “tomorrow” alone do
not select the planner. Ask when the intended target is unclear.

Run `./bin/day-planner` for the full command reference. Use this helper for every
planner read and mutation. Never edit planner Markdown by hand or reconstruct a
planner from memory when the helper fails.

This demo planner is local-only: it reads no calendar. The view has two kinds of
entries:

- Planner blocks are local time blocks.
- Commute blocks are local travel time that occupies time like a Planner block.

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
Blocks stay within one day. A commute `to` a planner block ends at its start; a
commute `home` begins at its end. Use `--keep-end` when resizing an outbound commute.

`task-add` fails when a section does not exist. Pass `--create` only if the user
clearly intends to create that section. Never turn a suggested schedule into
saved blocks or suggested priorities into checklist changes without authorization.

## Overlap confirmation

Adding, placing, moving, resizing, or creating commute blocks checks for overlaps
with existing local blocks. An overlap returns exit 4 and writes nothing. Explain
the specific conflicting items and obtain the user's confirmation before
repeating with `--allow-overlap`. Never pass that flag preemptively. A
confirmation covers that specific overlap, not unrelated future conflicts.
Calendar flags (`--count`, `--no-calendar`) and `excluded` do nothing here.

## Responses and state

Relay the helper's planner view in its existing order. After a mutation, give a
short confirmation; show the full view when asked. If it fails, report the failure
without inventing a replacement view. Translate backend errors into a short,
useful explanation; do not paste raw stderr or identifiers into normal replies.

State is local Markdown under `planner/`. Planner blocks are plans, not externally confirmed commitments, and do not automatically
create reminders or scheduled jobs. The optional `TARI_PLANNER_DIR` override is
for a separately isolated local state directory, including tests.

In ClawVoice, this directory must remain inside the active demo workspace;
symlink escapes are refused. The default planning zone is America/Toronto.
Day boundaries use IANA conversion, including 23/25-hour days. Local Markdown
blocks cannot distinguish two occurrences of a repeated clock hour: refuse
ambiguous/nonexistent local ranges, clarify with the user, and choose an
unambiguous range. Free/place excludes that clock-change interval.
