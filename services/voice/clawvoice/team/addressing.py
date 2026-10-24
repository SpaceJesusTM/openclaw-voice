"""Who is an utterance for? Name-addressed routing for a Team room.

Deterministic and transcript-based. An utterance is addressed to an agent when
it starts with the agent's name, after greetings and fillers ("<Name>, ...",
"Hey <Name> ...", "Okay <Name> ..."), or ends with it as a vocative
("..., <Name>?"). A name in the middle ("ask <Name> about it") is a mention,
not an address.
Nothing here looks at what is being asked: an unnamed request is for nobody.
Topic-based routing is deliberately absent.

Two separate addressed sentences for different agents ("<A>, what's on
tomorrow? <B>, build me a page.") become two requests, one per agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from clawvoice.voice.stop import is_stop_request

# Words that may come before a name without changing who is addressed.
_FILLERS = frozenset(
    "hey hi hello okay ok oh so and um uh erm hmm yo alright well right please now sorry yeah yes no "
    "thanks actually also then".split()
)
_FILLER_PAIRS = frozenset(
    {("thank", "you"), ("excuse", "me"), ("all", "right"), ("good", "morning"), ("good", "afternoon"), ("good", "evening")}
)
_MAX_FILLERS = 4
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_EDGE = " \t,.;:!?-–—"


@dataclass(frozen=True)
class Route:
    agent: str | None  # addressed agent id; None when no agent was named
    text: str  # the words for this agent, as spoken (name included)
    request: str  # what is asked, with the address removed
    bare: bool  # only the name was said ("Hey <Name>.")
    stop: bool  # only a request to stop speaking ("Stop." / "<Name>, stop.")
    how: str  # name | vocative | none; the room may set follow-up or continuation

    def to(self, agent: str, how: str) -> Route:
        return replace(self, agent=agent, how=how)


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower().replace("’", "'"), m.start(), m.end()) for m in _WORD.finditer(text)]


def _skip_fillers(words: list[tuple[str, int, int]]) -> int:
    i = skipped = 0
    while i < len(words) and skipped < _MAX_FILLERS:
        if words[i][0] in _FILLERS:
            i += 1
        elif i + 1 < len(words) and (words[i][0], words[i + 1][0]) in _FILLER_PAIRS:
            i += 2
        else:
            break
        skipped += 1
    return i


def only_fillers(text: str) -> bool:
    words = _words(text)
    return _skip_fillers(words) >= len(words)


class AddressBook:
    """Maps spoken names (and likely transcriptions of them) to agent ids."""

    def __init__(self, names: dict[str, list[str] | tuple[str, ...]]):
        self.agents = tuple(names)
        self._alias: dict[str, str] = {}
        for agent, aliases in names.items():
            for alias in aliases:
                key = str(alias).strip().lower()
                if key and key not in self._alias:
                    self._alias[key] = agent

    def agent_for(self, word: str) -> str | None:
        word = word.lower().replace("’", "'")
        if word.endswith("'s"):
            word = word[:-2]
        return self._alias.get(word)

    def _address(self, sentence: str) -> tuple[str | None, str, str]:
        """(agent, how, request) for one sentence."""
        words = _words(sentence)
        start = _skip_fillers(words)
        if start < len(words):
            agent = self.agent_for(words[start][0])
            if agent:
                return agent, "name", sentence[words[start][2] :].lstrip(_EDGE).strip()
        if len(words) >= 2:
            agent = self.agent_for(words[-1][0])
            if agent:
                between = sentence[words[-2][2] : words[-1][1]]
                if "," in between or words[-2][0] in _FILLERS:
                    request = (sentence[: words[-2][2]] + sentence[words[-1][2] :]).strip()
                    return agent, "vocative", request
        return None, "none", sentence.strip()

    def parse(self, text: str) -> list[Route]:
        """One route per addressed agent, in the order spoken; one unaddressed route otherwise."""
        text = " ".join(str(text or "").split())
        if not text:
            return []
        sentences = [s for s in _SENTENCE.split(text) if s.strip()]
        marks = [self._address(s) for s in sentences]
        groups: list[tuple[str, list[int]]] = []
        lead: list[int] = []
        for index, (agent, _how, _request) in enumerate(marks):
            if agent is not None and (not groups or groups[-1][0] != agent):
                groups.append((agent, [index]))
            elif groups:
                groups[-1][1].append(index)
            else:
                lead.append(index)
        if not groups:
            return [Route(None, text, text, False, is_stop_request(text), "none")]
        routes = []
        for number, (agent, indexes) in enumerate(groups):
            if number == 0:
                indexes = lead + indexes
            spoken = " ".join(sentences[i] for i in indexes)
            asked = " ".join(marks[i][2] if marks[i][0] == agent else sentences[i] for i in indexes).strip()
            how = next(marks[i][1] for i in indexes if marks[i][0] == agent)
            routes.append(Route(agent, spoken, asked, only_fillers(asked), is_stop_request(asked), how))
        return routes
