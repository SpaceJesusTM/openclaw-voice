# Tools

This demo has no Google, calendar or email integration.

## Local day planner and checklist

Read skills/tari-day-planner/SKILL.md and use ./bin/day-planner.
- ./bin/day-planner show tomorrow --json
- ./bin/day-planner free tomorrow --after 12:00 --before 18:00 --min 90m
- ./bin/day-planner add tomorrow 13:00-14:00 'Project work'
- ./bin/day-planner checklist --json

Examples are syntax, not instructions to save anything now. The planner writes
only local planner/days/YYYY-MM-DD.md and planner/checklist.md in this workspace.
It is local-only: it reads no calendar.
