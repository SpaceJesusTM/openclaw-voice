# Gmail "latest email" fix — 2026-10-24

Branch `fix/voice-gmail-latest`, from `5a78c91`. Gmail stays read-only; the
security filter is unchanged except that system labels (such as
`CATEGORY_UPDATES`) are kept, so Tari can say which tab a message is in.

## Root cause

The Gmail helper limited every default search to `in:inbox category:primary`.
In the Gmail API, `category:primary` matches only messages labelled
`CATEGORY_PERSONAL`. The Gmail **Primary tab**, however, shows every inbox
message that is not in a separately enabled tab. With Gmail's default tabs
(Primary, Promotions, Social), that includes Updates and Forums mail.

Measured on the demo mailbox (metadata only):

| Query | Newest result |
| --- | --- |
| `in:inbox category:primary` | 2025-07-21, `CATEGORY_PERSONAL` (the HelloFresh message Tari reported) |
| `in:inbox category:personal` | the same message: `primary` = `personal` in the API |
| `in:inbox` | 2026-10-23 22:29 Toronto, ChatGPT, `CATEGORY_UPDATES` |
| `in:inbox -category:promotions -category:social` | the same ChatGPT message |

The mailbox is the one shown in Gmail (same message, same time). The API
returned results newest first in every query, so ordering and `--max 1` were
not the cause; the scope was. The ChatGPT message really is labelled Updates
in the API. It appears in the Primary tab because the Updates tab is not
enabled, which is why Tari said "Updates, not Primary".

## Change

- `tari-google gmail search` now defaults to the Primary tab:
  `in:inbox -category:promotions -category:social`. If the owner enables other
  tabs, set `TARI_GMAIL_SEPARATE_TABS` in `infra/openclaw/.local/runtime.env`
  (for example `promotions,social,updates`) and restart the demo; the API cannot
  read tab settings.
- Results are sorted by receipt time (`internalDateIso`) instead of trusting
  the API's list order. A small `--max` first samples 20 results so "latest" and
  "last five" are the newest of a real sample.
- Each default search also checks the whole inbox and reports newer messages
  outside the Primary tab in `newer_outside_scope`, metadata only and
  sanitized. If that check fails, `newer_outside_scope_unknown` is set.
- The Gmail skill, retrieval guide and Tari's voice role say: never use
  `category:primary`, keep the newest-first order, state the message date,
  mention newer mail in other tabs, add `is:unread` only on request, and use
  `--all-mail` for explicit broader searches.
- `manage.py update-workspace` now also accepts the `5a78c91` templates as a
  known earlier version, so the demo upgrades safely.

## Why earlier tests missed it

The unit test only checked the query string the wrapper built, and the live
probe checked that Tari summarized the message returned by
`gmail search --max 1`. Both trusted the same wrong scope. Neither compared the
result with the newest message in the inbox by timestamp and label.

## Tests

- Offline (`infra/openclaw/tests/test_google.py`, fictional data):
  - the observed regression: a 2025 Personal message and a newer October 2026
    Updates message, where the 2026 one must be selected;
  - shuffled API order, where "last five" must be newest first;
  - the unread filter only on request;
  - newer Promotions mail reported rather than hidden, with a security message
    still withheld;
  - the tab override, and invalid values rejected.
- Live helper: the latest result and the last five equal an independent
  selection from a 50-message inbox list (filtered by labels, sorted by time).
- Live OpenClaw (`manage.py chat`): Tari named the ChatGPT message of
  Oct 23, 10:29 PM with an accurate summary.
- Live voice (`probes/p1_probe.py --live --gmail-only`): synthetic microphone →
  Higgs → OpenClaw → Gmail → spoken answer. The probe computes the newest
  Primary-tab message itself and fails if the answer describes an older one.

## Limitations

- The Primary-tab definition assumes Gmail's default tabs unless
  `TARI_GMAIL_SEPARATE_TABS` is set. The API cannot see the owner's tab settings
  or a non-tabbed inbox type (Important first, Priority Inbox).
- Gmail can recategorize a message; the API label at query time is used.
- "Newer outside the Primary tab" checks the 5 newest inbox messages, which is
  enough to tell whether newer mail exists, not to list all of it.
