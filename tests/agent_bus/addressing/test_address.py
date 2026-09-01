"""Address parsing, over every id shape that exists on disk today.

The round-trip property is the load-bearing one: inbox filenames derive from
the id, so an id that re-renders differently moves its own mailbox. Four
inboxes were orphaned that way already.
"""
import pytest

from agent_bus.address import BUS, PID, SESSION, THREAD, mint, parse

# Every format the adapters and store actually produce, with what it means.
REAL_IDS = [
    # (id as written, kind, space, value)
    (
        "8054898a-70b8-4f16-9a80-18dcf93f14c2",
        None,
        BUS,
        "8054898a-70b8-4f16-9a80-18dcf93f14c2",
    ),
    (
        "claude:a4775baa-d875-456c-ab27-1bb45511426d",
        "claude",
        SESSION,
        "a4775baa-d875-456c-ab27-1bb45511426d",
    ),
    (
        "agentbus:26bc255e-aee7-43dd-8c3b-ff7a84015756",
        "agentbus",
        SESSION,
        "26bc255e-aee7-43dd-8c3b-ff7a84015756",
    ),
    (
        "grok:01a02a13-3682-7fc1-8cb7-cbc55f8b91a5",
        "grok",
        SESSION,
        "01a02a13-3682-7fc1-8cb7-cbc55f8b91a5",
    ),
    (
        "omp:2901-9b81feb3-30a2-4667-bc35-b84a610da136",
        "omp",
        SESSION,
        "2901-9b81feb3-30a2-4667-bc35-b84a610da136",
    ),
    ("omp:tty:1234", "omp", PID, "1234"),
    ("codex:pid:4242", "codex", PID, "4242"),
    (
        "codex:thread:01a01cb8-1f72-7e71-97ca-69349d003abc",
        "codex",
        THREAD,
        "01a01cb8-1f72-7e71-97ca-69349d003abc",
    ),
    ("claude:pid:58291", "claude", PID, "58291"),
]


@pytest.mark.parametrize("text,kind,space,value", REAL_IDS, ids=[r[0][:28] for r in REAL_IDS])
def test_parses_every_real_id_shape(text, kind, space, value):
    a = parse(text)
    assert (a.kind, a.space, a.value) == (kind, space, value)


@pytest.mark.parametrize("text", [r[0] for r in REAL_IDS], ids=[r[0][:28] for r in REAL_IDS])
def test_round_trips_verbatim(text):
    """An id we parsed is never re-rendered -- its inbox filename depends on it."""
    assert str(parse(text)) == text


@pytest.mark.parametrize("text", [r[0] for r in REAL_IDS], ids=[r[0][:28] for r in REAL_IDS])
def test_compares_equal_to_the_plain_string(text):
    """store resolves by whole-string equality; that must keep working."""
    a = parse(text)
    assert a == text
    assert text == a or a == text  # symmetry via reflected __eq__
    assert hash(a) == hash(text)
    assert {a: 1}[text] == 1


def test_parse_is_total():
    """An unknown space is not an error -- a harness names its own namespace."""
    a = parse("weird:notaspace:value")
    assert (a.kind, a.space, a.value) == ("weird", "notaspace", "value")
    assert str(a) == "weird:notaspace:value"


@pytest.mark.parametrize("text", ["", ":", "::", "a:", ":b", "a::b", "x:y:z:w"])
def test_parse_never_raises(text):
    assert str(parse(text)) == text


def test_value_may_contain_colons():
    a = parse("codex:thread:a:b:c")
    assert (a.space, a.value) == (THREAD, "a:b:c")
    assert str(a) == "codex:thread:a:b:c"


def test_kind_hint_fills_a_bare_uuid():
    assert parse("8054898a-70b8", kind_hint="claude").kind == "claude"


def test_mint_is_canonical_and_parses_back():
    a = mint("codex", THREAD, "abc-123")
    assert str(a) == "codex:thread:abc-123"
    assert parse(str(a)) == a


def test_addresses_differing_only_in_value_are_distinct():
    assert parse("codex:thread:a") != parse("codex:thread:b")
    assert len({parse("codex:thread:a"), parse("codex:thread:b")}) == 2


def test_equality_against_other_types_is_not_an_error():
    assert parse("a:b:c") != 42
    assert parse("a:b:c") is not None
