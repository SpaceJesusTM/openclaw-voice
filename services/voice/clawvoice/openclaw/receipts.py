"""Split an agent reply into speakable text and its machine-readable receipt.

The ClawVoice request envelope asks the agent to finish with one line::

    CLAWVOICE_RECEIPT {"status": "done", "summary": "...", "changes": [...]}

The receipt is the agent's *claim* about what changed. ClawVoice treats it as
untrusted input: it only says which objects to read back; verification
decides what is true.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

MARKER = "CLAWVOICE_RECEIPT"
STATUSES = ("done", "needs_clarification", "failed", "no_change")
MAX_CHANGES = 10
MAX_SPEECH = 700


@dataclass
class ParsedReply:
    speech: str
    receipt: dict[str, Any] | None
    receipt_error: str | None = None


def speakable(text: str, limit: int = MAX_SPEECH) -> str:
    """Strip markdown/link noise and bound length at a sentence boundary."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: boundary + 1] if boundary > limit // 2 else cut.rstrip() + "…").strip()


def _validate(receipt: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(receipt, dict):
        return None, "receipt is not an object"
    status = receipt.get("status")
    if status not in STATUSES:
        return None, f"receipt status {status!r} is not one of {', '.join(STATUSES)}"
    changes = receipt.get("changes", [])
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        return None, "receipt changes must be a short list"
    clean_changes = []
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("type"), str):
            return None, "each receipt change needs a type"
        clean: dict[str, Any] = {}
        for key, value in change.items():
            if isinstance(value, str):
                clean[str(key)[:40]] = value[:300]
            elif isinstance(value, (int, float, bool)):
                clean[str(key)[:40]] = value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                clean[str(key)[:40]] = ",".join(item.strip() for item in value if item.strip())[:300]
        clean_changes.append(clean)
    summary = receipt.get("summary")
    return {
        "status": status,
        "summary": speakable(summary, 300) if isinstance(summary, str) else "",
        "changes": clean_changes,
        "question": speakable(receipt["question"], 300) if isinstance(receipt.get("question"), str) else "",
    }, None


def parse_reply(text: str | None) -> ParsedReply:
    text = text or ""
    index = text.rfind(MARKER)
    if index < 0:
        return ParsedReply(speech=speakable(text), receipt=None, receipt_error="no receipt line")
    before = text[:index]
    after = text[index + len(MARKER):]
    brace = after.find("{")
    if brace < 0:
        return ParsedReply(speech=speakable(before), receipt=None, receipt_error="receipt has no JSON object")
    try:
        receipt, end = json.JSONDecoder().raw_decode(after[brace:])
    except ValueError:
        return ParsedReply(speech=speakable(before), receipt=None, receipt_error="receipt JSON is invalid")
    trailing = after[brace + end:].strip(" `\n\r\t")
    valid, error = _validate(receipt)
    speech = speakable((before + (" " + trailing if trailing else "")).rstrip(" `\n:"))
    return ParsedReply(speech=speech, receipt=valid, receipt_error=error)
