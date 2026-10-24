# Approved Google integration

Use ./bin/tari-google for read-only Gmail and full Google Calendar event management:
- ./bin/tari-google calendar calendars
- ./bin/tari-google calendar list --start '2026-10-24T00:00:00-04:00' --end '2026-10-25T00:00:00-04:00' --json
- ./bin/tari-google gmail search 'newer_than:1d' --max 10
- ./bin/tari-google gmail read MESSAGE_ID
- ./bin/tari-google calendar manage create primary --summary 'Project planning' --from '2026-10-24T10:00:00-04:00' --to '2026-10-24T10:30:00-04:00'

Dates above are command examples, not today's date or real events. Create only
for an actual user scheduling request. Native Calendar management needs the separate
`tari-calendar-write` OAuth grant; reads use `tari-read`.
Google credentials and tokens belong to this installation only.
Use calendar manage for native create/update/delete, recurrence and attendee
options. Read back changes before claiming success. Gmail mutations are not supported.

## Local day planner and checklist

Read skills/tari-day-planner/SKILL.md and use ./bin/day-planner.
- ./bin/day-planner show tomorrow --json
- ./bin/day-planner free tomorrow --after 12:00 --before 18:00 --min 90m
- ./bin/day-planner add tomorrow 13:00-14:00 'Project work'
- ./bin/day-planner checklist --json

Examples are syntax, not instructions to save anything now. The planner writes
only local planner/days/YYYY-MM-DD.md and planner/checklist.md in this workspace.
It reads Calendar for conflicts and never invokes Calendar management.
