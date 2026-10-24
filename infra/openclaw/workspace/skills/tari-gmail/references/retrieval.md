# Gmail retrieval: bounded work and honest completeness

Read the parent skill first. Gmail remains read-only throughout. Read state is a
hint, never evidence that an item was handled or seen. These defaults are generic;
no production account's mailbox behavior or backlog has been imported.

## Choose the scope before searching

Default to the **Primary tab**, including both read and unread. The wrapper
searches `in:inbox -category:promotions -category:social` (Gmail's default tabs;
the owner can change the excluded tabs with `TARI_GMAIL_SEPARATE_TABS`). Do not
search `category:primary`: in the API it means only the Personal category, while
the Primary tab also shows Updates and Forums mail unless those tabs are enabled.

- Latest email: `gmail search --max 1`. Last N: `gmail search --max N`. Results
  are sorted newest first by receipt time; keep that order. Read the chosen
  message by ID before summarizing its content, and state its date.
- Do not impose a last-day window: the latest message may be older.
- Unread: add `is:unread` only when explicitly requested.
- Sender, subject or date: pass the criteria, e.g. `gmail search 'from:x' --max 5`;
  the wrapper keeps them in the Primary tab. If the user means any inbox tab or
  all mail, use `--all-mail 'in:inbox from:x'` or the exact broader query.
- Other categories, all mail, sent or archived: `--all-mail` with the exact query.
- Check `newer_outside_scope`: if it lists newer messages, say briefly that newer
  mail exists outside the Primary tab. Never present an old result as the
  newest mail without its date.

Use metadata first and start with a small page (for example 20). Open bodies only
for plausible actions, deadlines, changes, or requested detail. A quick on-demand
brief may deliberately stop at a bounded page, but must then say it covered that
bounded set and may omit other mail. It must never claim full inbox completeness.

An older-unread sweep is optional, not part of every brief. When useful, search
`is:unread older_than:1d` separately with a
small page. Explicitly call it a sample if not fully paginated. Do not reread the
entire backlog on each run or use unread-only searches for recent catch-ups.

## When the user asks for a complete recap

1. **Define the exact scope.** State its window and exclusions. If the user asks
   for all mail, include the categories the usual default would exclude.
2. **Query wide, filter precisely.** Relative Gmail operators are useful for a
   broad search. For an exact local day, use a wider window and compare each
   message's internal timestamp with explicit start/end instants in the user's
   timezone. Header `Date` is sender-supplied; Gmail date strings alone do not
   establish a local-day boundary. Account for daylight-saving offsets.
3. **Follow page tokens.** `--max` is a page size or limit, not a completeness
   claim. Inspect returned `nextPageToken`/pagination metadata and pass the next
   token using `--page` (upstream pagination is retained inside the wrapper's
   `data` object). Continue until exhausted, or disclose the precise limit.
   A wrapper error is not an empty page.
4. **Separate unread inventory when requested.** If asked about unread mail,
   retrieve that inventory in its own query; it may include messages outside the
   recent window. A recent-mail request alone does not authorize claiming that
   every older unread item has been checked.
5. **Merge and deduplicate by message ID.** A thread can contain multiple messages;
   account for the actual message results and do not count one message twice.
6. **Triage before opening.** Summarise routine messages from reliable metadata
   when sufficient; read bodies before asserting specific requested actions or
   deadlines. Group routine messages with counts. Every message inside the
   declared complete scope must appear individually or in a group.
7. **Disclose failures and limits.** A rate limit, failed body read, unknown
   timestamp, pagination cap, or truncated tool response means uncertainty.
   Explain which scope was covered and what remains unchecked. Stop rather than
   retry in a loop or hide missing results behind "nothing important".

## Cost, repetition, and targeted checks

Registration/RSVP checks should use narrow queries for the particular event, then
read the relevant confirmation. A generic invitation or unread label does not
prove registration or attendance. Avoid huge OR queries and broad body downloads.

For repeated explicitly enabled checks, preserve a small durable record of message
IDs already surfaced or judged routine, plus date and reason, in installation-local
state. Daily notes can record what was surfaced today. A cross-day ledger is needed
for backlog suppression; a daily note alone resets at midnight. Do not create a
second mailbox archive. Bound any ledger and store no bodies or credentials.

An item is worth repeating only after a material change: a reply, nearer actionable
deadline, changed schedule, or user-requested follow-up. "Still unread" is not a
change. When no repeated checks are enabled, no backlog ledger is needed.
