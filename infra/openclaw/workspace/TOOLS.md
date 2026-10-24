# Approved Google integration

Use ./bin/tari-google for read-only Gmail and Google Calendar:
- ./bin/tari-google calendar calendars
- ./bin/tari-google calendar list --start '2026-10-24T00:00:00-04:00' --end '2026-10-25T00:00:00-04:00' --json
- ./bin/tari-google gmail search 'newer_than:1d' --max 10
- ./bin/tari-google gmail read MESSAGE_ID

Dates above are command examples, not today's date or real events.
Google credentials and tokens belong to this installation only.
Do not use raw gog to bypass the read-only wrapper.
