# higgs-realtime vs higgs-realtime-1.5-preview

Measured on 2026-10-24 with `npm run probe -- compare` (identical TTS input audio for both models, a fresh session per trial) and one `npm run demo` run per model. Boson has published no release notes for 1.5. The docs still list only `higgs-realtime`.

## Single-turn results

Second run: 3 trials per model, 3 sessions at a time. The first run gave the same picture.

| | higgs-realtime | 1.5-preview |
|---|---|---|
| Voice: first audio after `response.create` (median, range) | 429 ms (339–552) | 949 ms (447–1759) |
| Voice: correct answers | 11/12 | 5/12 |
| Voice: correct, of responses that did not fail | 11/12 | 5/6 |
| Voice: failed responses (`upstream_error`) | 0/12 | 6/12 |
| Mind: decision time (median, range) | 610 ms (511–785) | 621 ms (592–1081) |
| Mind: right tool, of responses that did not fail | 9/9 | 5/5 |
| Mind: failed responses | 0/9 | 4/9 |
| Mind: filler text before the tool call | 0/9 | 3/9 |
| Server `web_search` | works | works |

**1.5 under load.** Its failures were all `upstream_error`, and both runs bunched them into the window where 3 sessions were generating at once. Two targeted runs settled it:

| 1.5 alone | Failed | First audio (median) | Mind decision (median) |
|---|---|---|---|
| 1 session at a time (15 sessions) | **0/14** | 537 ms | 617 ms |
| 3 sessions at a time (15 sessions) | **6/14** | 1710 ms | 1042 ms |

So the 1.5 preview has limited capacity for simultaneous responses; whether that limit is per key or Boson-wide is unknown. `higgs-realtime` had no failures at 3 at a time.

**How the answers sound.** These are the clearest quality differences. The WAVs are in `captures/compare-2026-10-24T00-35-57-248Z/`.

- **Spoken style.** `higgs-realtime` reads written-style text aloud: numbered markdown lists ("1. Create a dedicated workspace: Set up a specific area…") and step-by-step arithmetic ("Your meeting starts at 3:00 PM and runs for 90 minutes, so it ends at 4:30 PM. Then…"). 1.5 talks like a person: "First, set a dedicated workspace and stick to a routine. Second, take regular breaks to stretch and move around." and "You'll be free at 4:50 PM."
- **Math.** Both models sometimes answer "5:50" on the time question. 1.5 got it wrong in every completed trial of the second run, and 2 of 2 in the one-at-a-time run.
- **Spanish.** Both answer in Spanish, with the same answer.

## Multi-agent conversation (same scenario, one run each)

Bob wrongly believes the review is on Friday, and Alice owns the schedule.

- **higgs-realtime: polite and quick to agree.** Alice cut in, Bob yielded at once ("No worries, Alice. Go ahead."), and the rest was a thank-you loop ("Sounds good, Alice." / "Perfect, Bob.") until the turn budget stopped it.
  Transcript: `captures/demo-2026-10-24T00-38-30-283Z.md`
- **1.5-preview: an actual argument.** Bob held his position ("I know you keep the schedule, but I'm pretty sure the design review is Friday"), and they cut each other off over the date. Unprompted, it surfaced the real conflict: the bug needs a day, the review is Thursday. It ended with "I'll push hard to get the login crash fixed by Thursday, but I can't guarantee it." The minds' thoughts were more reasoned ("If the review is truly Thursday, the demo won't be ready"). It was also more stubborn: Bob treated "I think" as certain. No server failures in this run.
  Transcript: `captures/demo-2026-10-24T00-40-06-532Z.md`

## Summary

| | Better on |
|---|---|
| **1.5-preview** | Sounding like speech rather than read-out text. More assertive, opinionated agents that actually hash things out. More reasoned decisions. |
| **higgs-realtime** | Reliability (no failures at 3 at a time). Latency under load (about 0.4 s to first audio vs 1–1.7 s). Clean mind decisions (no filler, 9/9 right tool). |

A mix worth testing: **minds on `higgs-realtime`, voices on 1.5**. Decisions need speed and reliability but not spoken style. Voices are what you hear. It would also halve the concurrent load on 1.5, since both agents' minds decide at the same moment at the end of every turn.
