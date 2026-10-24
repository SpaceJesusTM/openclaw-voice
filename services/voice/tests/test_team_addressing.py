"""Team mode name routing: explicit names only, never the topic."""

import pytest

from clawvoice.team.addressing import AddressBook

BOOK = AddressBook(
    {
        "tari": ["tari", "tarry", "tary", "tahri", "tarri", "tori", "atari", "terry"],
        "astra": ["astra", "astro", "austra", "astrah", "ashra", "aster", "esther"],
    }
)


def route(text):
    routes = BOOK.parse(text)
    assert len(routes) == 1, routes
    return routes[0]


@pytest.mark.parametrize(
    ("text", "agent", "asked"),
    [
        ("Tari, what's on my calendar tomorrow?", "tari", "what's on my calendar tomorrow?"),
        ("Hey Tari, what's my next meeting?", "tari", "what's my next meeting?"),
        ("Okay Tari explain my schedule tomorrow", "tari", "explain my schedule tomorrow"),
        ("Astra, create a simple personal portfolio website.", "astra", "create a simple personal portfolio website."),
        ("Hey Astra, build me a simple portfolio website.", "astra", "build me a simple portfolio website."),
        ("Okay, Astra, what's the latest website update?", "astra", "what's the latest website update?"),
        ("HEY TARI WHAT'S ON MY PLANNER TODAY", "tari", "WHAT'S ON MY PLANNER TODAY"),
        ("So um hey Astra can you cancel the website task", "astra", "can you cancel the website task"),
        ("Atari, what's on my planner today?", "tari", "what's on my planner today?"),  # "Hey Tari" heard as one word
        ("Hey Astro, change the main heading.", "astra", "change the main heading."),
        ("What's on my calendar, Tari?", "tari", "What's on my calendar?"),  # vocative
    ],
)
def test_named_requests_reach_the_named_agent(text, agent, asked):
    r = route(text)
    assert (r.agent, r.request, r.bare, r.stop) == (agent, asked, False, False)


@pytest.mark.parametrize(
    "text",
    [
        "What's the weather like?",
        "Build me a website.",  # sounds like Astra's job, but nobody was named
        "What's on my calendar tomorrow?",  # sounds like Tari's, likewise
        "Can you ask Astra to build it?",  # a mention, not an address
        "I told Tari about it yesterday.",
    ],
)
def test_unnamed_requests_go_to_nobody(text):
    r = route(text)
    assert r.agent is None and not r.stop


def test_a_bare_name_only_opens_the_floor():
    r = route("Hey Tari.")
    assert r.agent == "tari" and r.bare and r.request == ""


def test_stop_is_global_unless_named():
    assert route("Stop.").agent is None and route("Stop.").stop
    assert route("That's enough.").stop
    named = route("Tari, stop talking.")
    assert named.agent == "tari" and named.stop
    assert not route("Tari, stop the timer task.").stop  # a request, not "stop speaking"


def test_two_separate_addressed_requests_split_by_agent():
    routes = BOOK.parse("Hey Tari, what's on tomorrow? And Astra, build me a login page.")
    assert [(r.agent, r.request) for r in routes] == [("tari", "what's on tomorrow?"), ("astra", "build me a login page.")]
    assert routes[0].text == "Hey Tari, what's on tomorrow?"


def test_follow_on_sentences_stay_with_the_named_agent():
    routes = BOOK.parse("Astra, make a portfolio site. Use a dark theme.")
    assert len(routes) == 1 and routes[0].agent == "astra" and "dark theme" in routes[0].text


def test_missing_space_after_punctuation_splits_separate_agent_requests():
    text = (
        "Okay, Tari, can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m."
        "Hey, Astro. What was the latest train change we made to the website? Okay, Astro. Can you?Revert that."
    )
    routes = BOOK.parse(text)
    assert [(r.agent, r.request) for r in routes] == [
        ("tari", "can you set a calendar event on October 31st for Halloween party from 8 to 9 p.m."),
        ("astra", "What was the latest train change we made to the website? Can you revert that?"),
    ]


def test_hesitation_question_marks_smooth_into_single_requests():
    assert route("Okay, Astro. Can you?Revert that.").request == "Can you revert that?"
    assert route("Hey, Tari. What do I have?Later today.").request == "What do I have later today?"
    assert (
        route("Hey, Ashra, what was the latest train change we made?To the website.").request
        == "what was the latest train change we made to the website?"
    )
    assert (
        route('Hey Astro, can you change the heading and title to "Welcome to"?To Astro Studio.').request
        == 'can you change the heading and title to "Welcome to Astro Studio"?'
    )
    assert (
        route("Okay, Tari, can you set a calendar event on October thirty first?First, for.").request
        == "can you set a calendar event on October thirty first, for."
    )


def test_unaddressed_non_filler_lead_does_not_contaminate_named_agent():
    routes = BOOK.parse("What was the latest change to the website? Tari, what's on my calendar?")
    assert len(routes) == 1
    assert routes[0].agent == "tari"
    assert routes[0].text == "Tari, what's on my calendar?"
    assert routes[0].request == "what's on my calendar?"
    # Pure filler lead sentences still attach cleanly.
    filler_lead = BOOK.parse("Hey. Tari, what's on my calendar?")
    assert len(filler_lead) == 1 and filler_lead[0].agent == "tari"


def test_thats_good_you_can_stop_now_is_recognized_as_stop():
    r = route("Okay, that's good. You can stop now.")
    assert r.stop and r.agent is None
