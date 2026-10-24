"""Recognize an explicit request to stop speaking.

Only a whole utterance that is just a stop request counts ("Stop.", "That's
enough", "You can stop that"). Anything with more content, such as "stop the
background task" or "stop, what's on tomorrow?", is a normal turn: task
cancellation keeps using cancel_task, and new questions are never suppressed.
"""

from __future__ import annotations

import re

_FILLER = r"(?:ok|okay|alright|all right|hey|please|no|yeah|yes|thanks|thank you|right|um|uh)"
_STOP = (
    r"(?:stop(?: talking| speaking| it| that| there| now| please)*"
    r"|(?:that'?s|that is|ok that'?s|okay that'?s) (?:enough|all|fine|good)(?: you can stop(?: now| talking| that| it)*)?"
    r"|enough(?: now)?"
    r"|you can stop(?: that| now| talking| there| it)*"
    r"|(?:please )?be quiet|quiet|shush|hush|shut up|never ?mind|that'?ll do)"
)
_PATTERN = re.compile(rf"^(?:{_FILLER} )*{_STOP}(?: {_FILLER}| now)*$")


def is_stop_request(text: str | None) -> bool:
    words = re.sub(r"[^a-z' ]+", " ", str(text or "").lower().replace("’", "'"))
    words = " ".join(words.split())
    return bool(words) and len(words) <= 60 and bool(_PATTERN.match(words))
