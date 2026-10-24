# Unified Calendar and day planner

New timed `day-planner add`, `place` and `commute-add` entries create a Google
Calendar event directly. There is no local scheduled copy to synchronize.
`show DAY --json` reads all Calendar entries live, with calendar/event IDs;
shared or declined entries appear as context without blocking free-time checks.
Move, resize, rename and remove resolve across Calendar and legacy local blocks.
The same event ID is retained when its date/time/title changes. Calendar edits
appear in the planner, and ClawVoice invalidates both schedule caches after jobs.

Existing local Markdown blocks (including Breakfast), IDs, metadata and checklist
are preserved. No migration or deduplication of existing data runs automatically.
If both a local block and Calendar event match a name, select a unique ID; do not
infer a link from the name. A clear request to edit a legacy local block edits
it locally; creating a Calendar replacement would require your approval.
Use `--local-only` only when explicitly requesting a new local-only block.

Examples (only execute for an actual requested entry):

```sh
python3 infra/openclaw/manage.py planner add tomorrow 08:00-09:00 'Breakfast'
python3 infra/openclaw/manage.py planner move tomorrow 'Breakfast' 10:00
python3 infra/openclaw/manage.py planner resize tomorrow 'Breakfast' 90m
python3 infra/openclaw/manage.py planner rename tomorrow 'Breakfast' 'Late breakfast'
python3 infra/openclaw/manage.py planner remove tomorrow 'Late breakfast'
```

Calendar writes are attempted once, then independently read back. A timeout or
mismatch reports an uncertain result and saves no local duplicate. Inspect the
Calendar before repeating a request. Concurrent external edits are not locked;
this is a live Calendar view, not a background synchronization service.
Recurring, all-day, multi-day and clock-change event edits use the existing
Calendar skill with explicit recurrence scope rather than the simple block CLI.
Checklist commands and overlap checks remain available.

All planning uses `America/Toronto`. On October 25, 2026, 08:00–09:00 Toronto is
12:00–13:00Z; after November 1 it is 13:00–14:00Z. UTC storage is correct and must
not be shifted again. New timed writes include IANA timezone metadata. Local
gaps and folds are refused rather than guessed.

The demo Calendar reports `UTC` as its Calendar timezone. If the UI shows noon
for an 8 AM Toronto event, open Google Calendar → Settings → Time zone → Primary
time zone and select Toronto/Eastern. No account setting or existing event is
changed by this fix. See [Google's display timezone guide](https://support.google.com/calendar/answer/37064).

## Commands and local data

Update the isolated existing demo with `python3 infra/openclaw/manage.py
update-workspace`. Known P1 templates are backed up and upgraded; customized
files stop the upgrade before any write. `init` still never overwrites existing
state. Start/restart ClawVoice after upgrading to load the new agent role.

`manage.py planner` runs `./bin/day-planner` inside the running demo container.
Use `planner COMMAND --help` for complete flags. Direct examples:

```sh
python3 infra/openclaw/manage.py planner show tomorrow --schedule
python3 infra/openclaw/manage.py planner free tomorrow --after 12:00 --before 18:00 --min 90m
python3 infra/openclaw/manage.py planner add tomorrow 13:00-14:00 "Project work"
python3 infra/openclaw/manage.py planner place tomorrow 90m "Write draft" --after 14:00
python3 infra/openclaw/manage.py planner move tomorrow "Project work" 16:00-17:00
python3 infra/openclaw/manage.py planner move today b2 --to-day tomorrow
python3 infra/openclaw/manage.py planner resize tomorrow b2 2h
python3 infra/openclaw/manage.py planner rename tomorrow b2 "Review draft"
python3 infra/openclaw/manage.py planner remove tomorrow @16:00
python3 infra/openclaw/manage.py planner commute-add tomorrow to "Team meeting" --duration 45m
python3 infra/openclaw/manage.py planner commute-add tomorrow home "Team meeting" --duration 45m
python3 infra/openclaw/manage.py planner checklist --json
python3 infra/openclaw/manage.py planner section-add "Personal"
python3 infra/openclaw/manage.py planner task-add "Personal" "Review prototype" --status priority
python3 infra/openclaw/manage.py planner task-status "Review prototype" in-progress
python3 infra/openclaw/manage.py planner task-complete "Review prototype"
python3 infra/openclaw/manage.py planner task-reopen "Review prototype"
python3 infra/openclaw/manage.py planner task-rename "Review prototype" "Review final prototype"
python3 infra/openclaw/manage.py planner task-remove "Review final prototype"
python3 infra/openclaw/manage.py planner section-remove "Personal"
```

These are independent usage examples; block IDs and available times depend on
saved state. Titles, stable IDs and `@HH:MM` references follow the original
resolver; ambiguous references are rejected. Default free/placement hours are
08:00–22:00. Checklist views support section/status filters. Completion is a
separate checkbox from optional `priority`, `start`, `in-progress` or `none`
status. Completing a task preserves its status; changing status never completes it.
Commutes occupy time and anchor their initial timing to an event/block start or
end; they are saved blocks, not a live dependency graph. Resize supports
`--keep-end` for preserving an arrival anchor.

## Storage and intentional adaptations

Actual host persistence:

```text
infra/openclaw/.local/state/workspace/planner/
  days/YYYY-MM-DD.md
  checklist.md
```

The container sees `/home/node/.openclaw/workspace/planner/`. Directories use
700 and files 600. Missing state is empty until a user requests a write. IDs,
sections and completion/status markers remain Markdown, with atomic individual
file replacement and conservative refusal of malformed or unreadable state.
The original cross-day move saves the destination before removing the source;
a storage failure between those writes can leave a duplicate. Inspect state
before retrying an uncertain operation. This remains a single-user CLI rather
than a concurrent transactional database.

`TARI_PLANNER_DIR` remains available for isolated state, but must name a dedicated
subdirectory inside this helper's workspace; workspace-root, outside paths and
symlinks are refused. Unit tests patch an isolated temporary workspace. Live
planner data is ignored by Git; accidental template planner data is ignored,
excluded from Docker build context and skipped during initialization.

Other adaptations are deliberately small: the manager's container forwarding,
targeted existing-workspace installation, strict Calendar completeness checks,
private atomic storage, and Toronto/DST handling. Legacy-block regression tests are retained alongside focused unified-schedule
coverage. Checklist semantics remain intact.

## Verification

```sh
python3 -m unittest discover -s infra/openclaw/tests -v
node --test apps/web/tests/microphone.test.cjs
cd services/voice
make check
# Opt-in live synthetic microphone, Gmail read and disposable Calendar lifecycle:
.venv/bin/python probes/p1_probe.py --live
```

Live evidence and physical microphone limitations are in
[voice-p1-fixes.md](voice-p1-fixes.md).
