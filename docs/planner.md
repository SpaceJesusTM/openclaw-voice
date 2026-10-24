# Tari day planner in ClawVoice

> **Local-only by default.** This demo has no Google integration, so the planner
> reads no Calendar: `show --json` reports `scope: "local"` and no Calendar warnings,
> and checked writes need no `--no-calendar`. The Calendar behaviour described below
> is the ported path, which runs only with `TARI_CALENDAR=google` plus a `tari-google`
> helper; neither is present in this repository.

The deterministic Tari Public planner is ported from commit
`c43455a43f59a554fd6e9e96934741ac4397a27d`. It retains daily blocks, commutes,
free-time placement and a separate categorized checklist. It runs on demand;
there are no scheduled briefs, automatic task imports or voice changes.

## Install and invoke

Fresh `manage.py init` installations include the helper and skill. For an
existing initialized installation:

```sh
python3 infra/openclaw/manage.py install-planner
python3 infra/openclaw/manage.py planner show today
python3 infra/openclaw/manage.py planner show tomorrow --json
```

The targeted installer preflights four workspace files, installs missing planner
code/skill and appends only planner sections to existing AGENTS.md and TOOLS.md.
It backs up changed existing files under ignored `.local/backups/`, writes
atomically, and refuses differing existing planner code/instructions for manual
review. It never replaces the configuration, credentials, sessions, helpers
or saved plans. Repeating the installation is a no-op. No runtime image rebuild
is needed because the workspace is a persistent bind mount.

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

## Ask Tari conversationally

```sh
python3 infra/openclaw/manage.py chat "Show my day planner for tomorrow." --conversation planner-demo
python3 infra/openclaw/manage.py chat "What free time do I have tomorrow afternoon?" --conversation planner-demo
python3 infra/openclaw/manage.py chat "Add a local planner block tomorrow from 1 PM to 2 PM for project work." --conversation planner-demo
python3 infra/openclaw/manage.py chat "Move that local block to 4 PM." --conversation planner-demo
python3 infra/openclaw/manage.py chat "Make that block two hours instead." --conversation planner-demo
python3 infra/openclaw/manage.py chat "Add a Personal checklist section, then add Review prototype there with priority status." --conversation planner-demo
python3 infra/openclaw/manage.py chat "I finished Review prototype. Show my checklist." --conversation planner-demo
```

A conflict may correctly prevent a requested save. Tari reads the planner skill,
runs the helper and checks its result before claiming success. A clear local
planner request authorizes that change; advice alone saves nothing. If the
requested destination is ambiguous, Tari asks whether it is a local block,
checklist task or actual Calendar booking. Use the same conversation ID for
follow-ups. Combined Calendar/planner/checklist overviews use `show DAY --json`;
no separate briefing subsystem is necessary.

## Three sources and Calendar coverage

- **Calendar:** external bookings, managed by the existing Calendar skill and
  unchanged `tari-google calendar manage` native gogcli interface.
- **Planner/commute:** local time allocations. Creating, moving or deleting them
  never creates or changes Calendar events.
- **Checklist:** persistent local tasks; neither Calendar nor a day schedule is
  automatically populated from them or from model suggestions.

The planner only calls `tari-google calendar list --start ... --end ... --json`.
The wrapper retains all its native Calendar management, recurrence and attendee
capabilities, with no new scopes, permission engine or enable flag.
The planner consumes its aggregated event/calendar identity and attendance data.
Primary events count as occupied unless cancelled, declined or transparent.
Accepted shared invitations count; other shared/subscribed events are context
and excluded from normal busy-time views. `--count EVENT_ID` explicitly includes
a selected shared event. All-day events are informational, not whole-day busy
blocks. Recurring instances returned by the wrapper use the same inclusion rules.

Calendar/local overlaps reject checked saves without changing files. The error
identifies the conflict. Use `--allow-overlap` only after the user accepts that
specific overlap. Missing, malformed, paginated/incomplete or partially failed
Calendar reads reject Calendar-dependent mutations; a failed read never means
free time. `--no-calendar` is reserved for explicit permission to save unchecked.
Read-only views can show local state with warnings; JSON reports `complete`,
`calendar_complete`, `scope`, `data_status` and structured warnings. An explicitly
local-only view has `calendar_complete: false` even if its local scope is complete.
Free-time output during an outage is labelled an unverified estimate.

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
private atomic storage, and Toronto/DST handling. All 34 original planner tests
are retained, alongside focused integration regressions. Original command and
checklist semantics remain intact.

## Timezone investigation and DST

The existing **Planner Integration Smoke Test** on October 26, 2026 was read
through the existing Calendar API wrapper before code changes:

| Value | Observed / equivalent |
| --- | --- |
| Stored start | `2026-10-26T19:00:00Z` |
| Stored end | `2026-10-26T19:15:00Z` |
| Toronto conversion | `15:00–15:15`, offset `-04:00` |
| Event timezone metadata | `Etc/GMT+4` |
| Source Calendar timezone | `UTC` |

The stored absolute instants are correct. The reported 19:00 UI display is
consistent with UTC display settings, not a four-hour event-writing error. The
UI setting itself was not inspected. No event or Calendar timezone was changed.
In Google Calendar web Settings → Time zone → Primary time zone, choose Toronto
(Eastern time). Calendar metadata and the UI's display zone are separate;
see [Google's timezone instructions](https://support.google.com/calendar/answer/37064).
The fixed-offset metadata is correct for this single October event; use an IANA
zone for new recurring bookings so their local time follows DST.

Planning uses `TARI_TIMEZONE=America/Toronto`, with IANA conversion and local
midnight boundaries. Tests verify 23-hour March 8 and 25-hour November 1 days,
and UTC appointments rendering at 15:00 on dates before/after both transitions.
Nonexistent spring-forward times and ambiguous fall-back times are refused for
local blocks, including spans crossing those wall-clock regions. Auto-placement
excludes those regions. Markdown cannot distinguish two copies of a repeated
hour; choose an unambiguous local interval. Real Calendar events retain exact
instants/offsets; busy-time checks conservatively occupy the repeated hour when
an event crosses the backward clock change. This can exclude extra free time,
but never advertises ambiguous time as safely available.

## Validation on the isolated demo

Run the offline suite from repository root:

```sh
python3 -m unittest discover -s infra/openclaw/tests -v
python3 -m py_compile infra/openclaw/manage.py infra/openclaw/workspace/bin/day-planner
python3 infra/openclaw/manage.py doctor
```

Offline tests use fictional Calendar responses and temporary state, including
legacy installation upgrades, all planner operations, inclusion rules, failure
paths, safe storage and DST. They never call the live Google account.
Live acceptance and agent tool evidence are recorded in the
[foundation audit](openclaw-audit.md#planner-integration-acceptance).
Private raw responses, event identities and validation journals stay in ignored
`.local/`; public documentation contains no personal schedule/mail content.
