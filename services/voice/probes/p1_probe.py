"""Opt-in P1 regression over synthetic microphone -> Higgs -> OpenClaw -> Google.

.venv/bin/python probes/p1_probe.py --live [--gmail-only] [--url http://127.0.0.1:7860]
Private conversation evidence stays in ignored .local/. Stdout contains no mail.
Creates only a unique disposable event tomorrow; cleans it up even on failure.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from voice_probe import (
    TZ, Probe, calendar_events_on, greet, helper_json, norm,
    require, settle_jobs, unique, wait_job, wav,
)


async def spoken_job(p, words, label):
    offset = p.mark()
    p.mic.play(wav(f"p1-{label}-{hashlib.sha256(words.encode()).hexdigest()[:12]}", words))
    job = await wait_job(p, offset, label, 360)
    await p.wait_turn(p.mark(), f"{label} announcement", timeout=90)
    require(job["status"] == "completed", f"{label} did not complete")
    return job


SEPARATE_TABS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}


def newest_primary_tab_messages():
    """Pick the newest Primary-tab messages ourselves: a broad inbox list, filtered by
    category labels and sorted by receipt time, not the wrapper's scoped search."""
    rows = helper_json("google", "gmail", "search", "in:inbox", "--all-mail", "--max", "50")["data"]["messages"]
    qualifying = [r for r in rows if not SEPARATE_TABS & set(r.get("labels", []))]
    return sorted(qualifying, key=lambda r: r["internalDateIso"], reverse=True)


def subject_tokens(row):
    return [t for t in norm(row.get("subject", "")).split()
            if len(t) > 4 and t not in {"security", "related", "content", "withheld"}]


async def check_latest_email(p):
    expected = newest_primary_tab_messages()
    require(expected, "Primary tab has no messages for live validation")
    newest = expected[0]
    scoped = helper_json("google", "gmail", "search", "--max", "1")["data"]["messages"]
    require(scoped and scoped[0]["id"] == newest["id"], "helper latest is not the newest Primary-tab message")
    job = await spoken_job(p, "What's the most recent email in my Primary Inbox?", "gmail")
    text = norm(job.get("outcome_text", ""))
    require(not re.search(r"cannot access|can t access|don t have access|unable to access", text), "voice declined email access")
    security = "security-related content withheld" in json.dumps(newest)
    require(security or any(t in text for t in subject_tokens(newest)), "answer does not describe the newest Primary-tab message")
    # Regression: an older message must not be presented as the latest.
    older = [r for r in expected[1:] if subject_tokens(r) and not set(subject_tokens(r)) & set(subject_tokens(newest))]
    wrong = [r["id"] for r in older if all(t in text for t in subject_tokens(r)[:2])]
    require(not wrong, f"answer describes an older message instead: {wrong}")
    return {"status": "PASS", "newest_id": newest["id"], "newest_received": newest["internalDateIso"],
            "category": [label for label in newest.get("labels", []) if label.startswith("CATEGORY_")],
            "security_withheld": security, "read": "UNREAD" not in newest.get("labels", [])}


async def main(url, gmail_only=False):
    title = unique("ClawVoice Unified")
    renamed = title + " Updated"
    tomorrow = datetime.now(TZ).date() + timedelta(days=1)
    p = Probe(url, 90)
    result = {}
    before_ids = {e["id"] for e in calendar_events_on(tomorrow)}
    created = None
    test_titles = {title, renamed}
    try:
        await p.connect()
        await greet(p, url)
        result["gmail"] = await check_latest_email(p)
        if not gmail_only:  # unified schedule lifecycle (disposable Calendar event)
            # Breakfast already exists locally at 8; do not modify it or overlap it.
            schedule = helper_json("planner", "show", tomorrow.isoformat(), "--json")
            require(schedule["complete"], "schedule incomplete")
            for start, end in [(600, 660), (720, 810)]:
                require(not [i for i in schedule["schedule"] if not i.get("allday") and not i.get("free") and i["start"] < end and start < i["end"]], "disposable test slot occupied")
            job = await spoken_job(p, f"Add a planner block tomorrow from 10 to 11 AM called {title}.", "create")
            require(job["verification"] == "verified", "planner create not verified")
            heard_title = job["title"].split(": ", 1)[-1]
            test_titles.add(heard_title)
            entries = [e for e in calendar_events_on(tomorrow) if e["id"] not in before_ids and e.get("summary") == heard_title]
            require(len(entries) == 1, "planner did not create exactly one Calendar event")
            eid = entries[0]["id"]
            created = (entries[0]["calendar_id"], eid)
            title = heard_title
            rows = helper_json("planner", "show", tomorrow.isoformat(), "--json")["schedule"]
            require(len([e for e in rows if e["title"] == title]) == 1, "planner duplicated scheduled item")
            offset = await p.say("What is on my planner tomorrow?")
            await p.wait_turn(offset, "fresh planner answer")
            require(p.tools(offset, "get_context"), "no cached context read")
            require(norm(title) in norm(p.bot_text(offset)), "cache did not refresh after create")
            for words, label in [
                (f"Move only the Calendar event named {title} tomorrow to noon, keeping its one hour duration. Leave all other entries unchanged.", "move"),
                (f"Resize only the planner block named {title} tomorrow to ninety minutes, keeping its start time. Leave all other entries unchanged.", "resize"),
                (f"Rename only the planner block named {title} tomorrow to Unified Schedule Test Updated. Leave all other entries unchanged.", "rename"),
            ]:
                job = await spoken_job(p, words, label)
                require(job["verification"] == "verified", f"{label} not verified")
                entries = [e for e in calendar_events_on(tomorrow) if e["id"] == eid]
                require(len(entries) == 1, "Calendar identity changed")
            start = datetime.fromisoformat(entries[0]["start"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
            end = datetime.fromisoformat(entries[0]["end"]["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
            require((start.hour, start.minute, end.hour, end.minute) == (12, 0, 13, 30), "Toronto read-back mismatch")
            renamed = entries[0]["summary"]
            require("updated" in renamed.lower(), "rename missing")
            job = await spoken_job(p, f"Delete only the planner block named {renamed} tomorrow. Leave all other entries unchanged.", "delete")
            require(job["verification"] == "verified", "delete not verified")
            require(not [e for e in calendar_events_on(tomorrow) if e["id"] == eid], "test event still exists")
            result["schedule"] = {"status": "PASS", "same_identity": True, "no_duplicate": True, "cache_refreshed": True,
                                  "start_local": start.isoformat(), "end_local": end.isoformat(), "deleted": True}
    except Exception as exc:  # noqa: BLE001 - save evidence and clean disposable data
        result["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        await p.close()
        await settle_jobs(url)
        # Resolve only this probe's new IDs and returned titles, including ASR spelling.
        for event in calendar_events_on(tomorrow):
            if event["id"] not in before_ids and (event.get("summary") in test_titles or
                                                   created == (event["calendar_id"], event["id"])):
                helper_json("google", "calendar", "manage", "delete", event["calendar_id"], event["id"], "--send-updates", "none")
                absent = helper_json("google", "calendar", "get", event["calendar_id"], event["id"], "--allow-absent")["event"]
                require(absent.get("absent") or absent.get("status") == "cancelled", "cleanup unverified")
        private = Path(__file__).resolve().parents[1] / ".local/p1-live.json"
        private.write_text(json.dumps({"result": result, "session": p.report()}, default=str))
    print(json.dumps(result, indent=2))
    return int("failure" in result)


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--live", action="store_true")
    args.add_argument("--url", default="http://127.0.0.1:7860")
    args.add_argument("--gmail-only", action="store_true", help="only the latest-email check (no Calendar writes)")
    options = args.parse_args()
    if not options.live:
        args.error("Pass --live to allow real provider access and disposable Calendar writes")
    raise SystemExit(asyncio.run(main(options.url, options.gmail_only)))
