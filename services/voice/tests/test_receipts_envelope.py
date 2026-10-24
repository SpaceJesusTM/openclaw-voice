from datetime import datetime
from zoneinfo import ZoneInfo

from clawvoice.jobs.models import Expectations, Job
from clawvoice.openclaw.envelope import build_message
from clawvoice.openclaw.receipts import parse_reply, speakable
from clawvoice.timeutil import FrozenClock
from conftest import tari_fixture_config


def test_receipt_is_split_from_speech():
    text = (
        "**Done!** I added *Project Meeting* tomorrow from 3 to 4 PM. See https://calendar.google.com/x\n"
        'CLAWVOICE_RECEIPT {"status":"done","summary":"Added it.","changes":[{"type":"calendar_event",'
        '"action":"created","calendar_id":"primary","event_id":"abc123"}]}'
    )
    parsed = parse_reply(text)
    assert parsed.receipt["status"] == "done"
    assert parsed.receipt["changes"][0]["event_id"] == "abc123"
    assert parsed.speech == "Done! I added Project Meeting tomorrow from 3 to 4 PM. See a link"
    assert "CLAWVOICE" not in parsed.speech


def test_receipt_failures_are_explicit():
    assert parse_reply("Just words").receipt_error == "no receipt line"
    assert parse_reply("x\nCLAWVOICE_RECEIPT {not json").receipt_error == "receipt JSON is invalid"
    bad_status = parse_reply('CLAWVOICE_RECEIPT {"status":"probably","changes":[]}')
    assert bad_status.receipt is None and "status" in bad_status.receipt_error
    too_many = parse_reply('CLAWVOICE_RECEIPT {"status":"done","changes":' + str([{"type": "x"}] * 11).replace("'", '"') + "}")
    assert too_many.receipt is None
    fenced = parse_reply('Okay.\n```\nCLAWVOICE_RECEIPT {"status":"no_change","summary":"Nothing to do","changes":[]}\n```')
    assert fenced.receipt["status"] == "no_change" and fenced.speech == "Okay."


def test_speakable_bounds_length_at_sentence():
    text = "First sentence is here. " * 60
    result = speakable(text, 120)
    assert len(result) <= 120 and result.endswith(".")


def test_envelope_frames_change_with_transcript_expectations_and_receipt_formats():
    agent = tari_fixture_config()
    job = Job(
        id="J1A2B3", agent_id="tari", owner_scope="local-owner", session_key=agent.session_key, kind="change",
        target="calendar", request="Create a Calendar event tomorrow from 3 to 4 PM called Project Meeting",
        title="Calendar: Project Meeting",
        expectations=Expectations(title="Project Meeting", date="2026-10-24", start_time="15:00", end_time="16:00").present(),
        user_words="Tari, create a calendar event tomorrow from three to four PM called project meeting.",
        origin_session="s1", tool_call_id="c1", fingerprint="f", idempotency_key="k",
    )
    clock = FrozenClock(datetime(2026, 10, 23, 23, 50, tzinfo=ZoneInfo("America/Toronto")))
    message = build_message(agent, job, clock)
    assert "Local time: Friday, October 23, 2026, 11:50 PM (America/Toronto)." in message
    assert "CHANGE: the owner explicitly asked for this change" in message
    assert 'Owner\'s latest words (speech transcript, may contain recognition errors): "Tari, create' in message
    assert 'destination: calendar; title "Project Meeting"; date 2026-10-24; from 15:00 to 16:00 (America/Toronto)' in message
    assert "ask one short question instead" in message
    for rtype in agent.receipt_types.values():
        assert rtype.format in message
    assert message.count("CLAWVOICE_RECEIPT") == 1


def test_question_envelope_forbids_changes():
    agent = tari_fixture_config()
    job = Job(id="J0", agent_id="tari", owner_scope="o", session_key="k", kind="question", target="other",
              request="What did we decide yesterday?", title="t", expectations={}, user_words="", origin_session=None,
              tool_call_id=None, fingerprint="f", idempotency_key="k")
    message = build_message(agent, job, FrozenClock(datetime(2026, 10, 23, 12, 0, tzinfo=ZoneInfo("UTC"))))
    assert "QUESTION: answer it by looking it up with your tools (read-only)" in message
    assert "Do not create, change or delete anything." in message
    assert "Say which date range or scope you checked." in message
    assert "Details the voice assistant resolved" not in message
