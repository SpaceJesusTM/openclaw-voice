# ClawVoice P1 microphone test fixes — 2026-10-24

Scope: isolated Mac demo, starting at `f884ea4`. Production Tari, OAuth grants,
account timezone settings and unrelated Calendar events are unchanged. Legacy
planner data is restored byte-for-byte at handoff (see the probe finding below).

## Causes and changes

1. **Gmail routing.** A real Higgs session refused email access without calling
   any tool. The demo Gmail helpers and existing authorization worked. Tari's
   voice role did not explicitly route email retrieval, its target list omitted
   Gmail, and its retrieval guide discouraged Primary-only searches. The role
   now delegates Gmail questions to OpenClaw. The helper defaults to
   `in:inbox category:primary`, without unread or last-day restrictions. Explicit
   broader queries use `--all-mail`; unread is added only on request. The
   read-only profile/command guards remain; security filtering also withholds
   sign-in alerts, two-factor/backup-code and account-security content.

2. **Separate schedules.** Timed planner mutations wrote only Markdown;
   Calendar was read for conflicts, with unaccepted shared entries omitted.
   New timed planner/commute entries now write Calendar directly, with private
   kind metadata and a Calendar event receipt. The same calendar/event identity
   is used for subsequent move, resize, rename and delete. No local mirror,
   background synchronization or automatic migration is introduced. All Calendar
   entries are visible; context-only entries do not block availability or become
   the next commitment. Writes get independent read-back; uncertain outcomes
   are inspected rather than automatically retried. Schedule job invalidation
   refreshes both Calendar and planner caches.

   Breakfast remains a local 08:00–09:00 block on October 25. It was not migrated;
   a probe briefly changed it and restoration was independently checked below. Legacy blocks/checklist still use the existing local
   storage and metadata. Ambiguous local/Calendar matches require a unique ID.

3. **Toronto display.** Calendar discovery reports `UTC`. No Breakfast Calendar
   event was found in the October 25 retrieval window, so its original save
   cannot be independently diagnosed from a surviving event. The current local
   Breakfast and new disposable events demonstrate correct Toronto conversion.
   Existing offset-aware conversion did not need a second shift. Timed writes
   now default to IANA `America/Toronto` metadata rather than a fixed offset.
   Live 08:00 bookings read back as 12:00Z on October 26 and 13:00Z on November 2;
   both were deleted and independently confirmed absent. Offline coverage
   includes October 25, spring gaps, fall folds and 23/25-hour day boundaries.

4. **Activation.** ClawVoice supplied only `server_vad` unless tuning was set,
   relying on Boson's defaults. The pinned SDK/server default is 0.85
   ([Boson changelog](https://github.com/boson-ai/pipecat-boson/blob/main/CHANGELOG.md)).
   ClawVoice now explicitly uses threshold 0.90 and silence 600 ms. Browser echo
   cancellation/noise suppression remain enabled; automatic gain is disabled
   to avoid boosting room noise. Optional push-to-talk gates the browser audio
   track, including release, focus loss, hidden-tab and pointer cancellation.
   Pipecat, native barge-in and both realtime compatibility fixes are preserved.

## Validation

- Foundation: **90 tests passed** (original legacy-block coverage plus unified
  lifecycle, identity, duplicates, partial writes, malformed-state protection,
  Primary defaults, security filtering and safe workspace upgrades).
- Voice: **112 tests passed** in `make check`, including shared-context coverage.
  Ruff and Python compilation pass.
- Browser microphone gate: **2 Node tests passed**; JavaScript syntax checks pass.
- Live browser: UI connection/model verification, typed and synthetic spoken
  turns, push-to-talk track gating, cache lookup, task panel and disconnect pass.
- Live voice scenario A: exact `higgs-realtime-1.5-preview` acknowledgement,
  synthetic microphone input, non-silent reply, native barge-in/truncation and
  reconnect pass (48.5 seconds).
- Live Gmail: synthetic microphone → Higgs → OpenClaw → Gmail search/read →
  spoken summary passes. The latest Primary message was already read, and a
  source fact matched the independently fetched message. Read-only inspection
  of the demo's local OpenClaw transcript confirms search and body-read helper
  calls. No mail content is included in this report.
- Live unified lifecycle: see final run evidence below.

Private reports remain in ignored `services/voice/.local/` and `captures/`.
An initial schedule probe failed because speech recognition changed its random
name; it had created a verified event successfully. That disposable event was
identified from the job's returned title, deleted and confirmed absent. The
probe now follows the returned identity and cleans up by event ID.

A later probe used "that planner block" after a planner overview; Higgs resolved
it to the unrelated local Breakfast block. It resized and renamed that block.
Breakfast was immediately restored, and SHA-256 manifests confirmed every
original planner/checklist file matched its pre-test bytes exactly. The voice
and OpenClaw guidance now keeps "that event/block" tied to the last changed item
and asks when unclear. Rename expectations now explicitly require the new title,
fixing a false verification mismatch. The final probe uses explicit disposable
names for each mutation. Natural-language reference resolution remains
probabilistic; name an entry when several could be meant.

## Startup and manual checks

From the repo root:

```sh
python3 infra/openclaw/manage.py update-workspace
python3 infra/openclaw/manage.py start
cd services/voice
make dev
```

Stop/restart an existing voice server to load the updated role/configuration.
Open http://127.0.0.1:7860. The workspace updater backs up known P1 templates,
refuses customized files before writing and never modifies planner data or OAuth.
No setup/reset of existing credentials is needed.

With your physical microphone/headphones, test a quiet sentence, room noise,
nearby speech and interruption. Reconnect after changing
`BOSON_VAD_THRESHOLD`/`BOSON_VAD_SILENCE_MS` in `services/voice/.env`.
Try 0.85 for quiet/distant speech or 0.95 for louder/noisier input. Use push-to-talk
in busy rooms; VAD does not identify the owner or reject every nearby speaker.
Boson noise reduction remains an optional setting; hardware/browser support for
capture constraints varies. Physical microphone sensitivity is not validated by
synthetic speech tests.

In Google Calendar → Settings → Time zone → Primary time zone, select Toronto
(Eastern time) if the UI shows UTC. See [Google's timezone guide](https://support.google.com/calendar/answer/37064).
08:00 Toronto on October 25 is 12:00 UTC; do not move the event to compensate.
No account setting is changed automatically.

Existing legacy local blocks remain local until you explicitly approve a
migration; there is no automatic reconciliation of old duplicates. Recurring,
all-day, multi-day and clock-change event edits use the existing Calendar skill
with explicit scope. Concurrent external edits are not locked. Outside changes
refresh on the existing cache TTL; voice jobs invalidate affected views immediately.
Live Google tests use HTTP fallback; WebSocket device approval remains optional.

Repeat opt-in integration tests against your running demo:

```sh
cd services/voice
.venv/bin/python probes/p1_probe.py --live
.venv/bin/python probes/voice_probe.py --live --scenario a
node probes/ui_probe.mjs --live
```
