"""The watch cycle: a line arrives, the reader gets the message, acks, answers.

agent-bus delivers information, in real time or close to it. What a harness
does with a delivery is the harness's business — some start a turn on it, some
return from a blocked call, some read it when they next look. That composition
is a desirable capability where it exists and cannot be asserted here, because
not every harness has it. What *is* ours, and what this file is about, is the
cycle: watch emits, the reader reads, acks, replies.

Read from the reader's side, and the reader may have no MCP tools at all — pi
has none and works. So the text output is the surface: an agent should never
be told to ask for `--json` and take it apart. `test_agent_facing_surface.py`
guards what that text may say; this guards whether it is enough to act on.

Measured 2026-08-28: the watch line carried an id `ack` rejected, `inbox` cut
the body at 200 characters with no command for the rest, and the agent went to
`--json` and then to a file inside the user's git tree. Five of those six steps
were recovery from the one before.

Two tests here are still skeletons, and say why in their bodies.
"""

import json
import os
import subprocess

import pytest

from agent_bus import store
from agent_bus.cli import main
from agent_bus.store import RosterEntry
from agent_bus.store import register as store_register
from agent_bus.watch import format_event

SENDER = "someone"
READER = "reader"
SUMMARY = "the short form"
BODY = "the whole body, which is longer than any preview. " * 8


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def holder():
    """A live process to hang a roster entry on, so pruning leaves it alone."""
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
def delivered(bus, holder, capsys):
    """One message, sent. Returns the full id and the notice line for it."""
    store_register(SENDER, "other", pid=os.getpid(), home=bus)
    store_register(READER, "other", pid=holder.pid, home=bus)
    main(["send", READER, "-m", BODY, "--summary", SUMMARY, "--from-name", SENDER])
    capsys.readouterr()

    assert main(["inbox", "--address", READER, "--json"]) == 0
    msg = json.loads(capsys.readouterr().out)[0]
    return msg["id"], format_event(msg)


def _out(capsys, argv, expect=0):
    assert main(argv) == expect
    return capsys.readouterr().out


# ------------------------------------------------------------- the watch line


def test_the_watch_line_carries_an_id_the_reader_can_act_with(delivered, capsys):
    """`watch` prints the first eight characters and has since it existed;
    `ack` matched the whole id and had since v0.1.0. So the id on a delivery
    notice was never one the reader could act with."""
    _, line = delivered
    shown = line.split("id=")[1].split()[0]

    assert _out(capsys, ["ack", shown, "--address", READER]).strip() == "marked read"


def test_the_watch_line_carries_the_summary_and_not_the_body(delivered):
    """A delivery notice. The body is what reading is for — and a webhook
    arrives as one of these, where the summary is the whole of it."""
    _, line = delivered
    assert SUMMARY in line
    assert BODY[:40] not in line


def test_the_watch_line_names_the_sender_a_reply_can_be_addressed_to(delivered):
    """Whoever wrote is who you answer."""
    _, line = delivered
    assert f"from={SENDER}" in line


def test_a_sender_that_never_registered_can_still_be_answered(bus, holder, capsys, monkeypatch):
    """Measured: a live Claude session that had never registered arrived as
    `from anonymous`, and `send anonymous` fails with `no such agent`. #140:
    an unregistered sender's identity comes from discovery -- the same
    ancestor-pid walk `self` already uses (#125) -- so it arrives under a
    name `send` can address back, not one that dead-ends the reply."""
    store_register(READER, "other", pid=holder.pid, home=bus)

    # kind="other" keeps this scoped to the identity question #140 is about
    # -- addressing a real Claude peer over its socket is a different
    # mechanism, covered where the claude transport adapter is tested.
    driver_pid = os.getpid()
    session = RosterEntry(
        id="claude:live-session", name="never-registered-peer", kind="other",
        pid=driver_pid, cwd=None, status="idle", inbox={}, native={},
        registeredAt="", updatedAt="",
    )
    monkeypatch.setattr(store, "ancestor_pids", lambda start=None: [driver_pid])
    monkeypatch.setattr(store, "discover_agents", lambda home=None: [session])

    assert _out(capsys, ["send", READER, "-m", "wake test"]) == "sent to reader\n"

    assert main(["inbox", "--address", READER, "--json"]) == 0
    msg = json.loads(capsys.readouterr().out)[0]
    line = format_event(msg)
    assert "from=never-registered-peer" in line
    assert "anonymous" not in line

    reply = _out(capsys, ["send", "never-registered-peer", "-m", "ack", "--from-name", READER])
    assert reply == "sent to never-registered-peer\n"


def test_the_watch_line_points_at_the_tools_when_the_session_has_them():
    """Skeleton. The line still carries sender, id and summary — that does not
    change. What changes is which surface the next step names: `get_inbox`
    where the session has our tools, the CLI where it does not."""


# ------------------------------------------------------------------- reading


def test_a_reader_can_get_one_whole_message_by_id(delivered, capsys):
    """Nothing truncates it. A body is capped at MAX_TEXT when it is sent,
    which is what makes printing it whole safe."""
    full_id, _ = delivered
    assert BODY.strip() in _out(capsys, ["read", full_id, "--address", READER])


def test_a_reader_can_read_by_the_prefix_the_notice_gave(delivered, capsys):
    full_id, _ = delivered
    assert BODY.strip() in _out(capsys, ["read", full_id[:8], "--address", READER])


# Markers chosen so a failure names the defect. `C` sits past the first 200
# characters, so a truncating reader keeps `B` and loses it.
SUBJECT_MARK = "A" * 8
BODY_MARK = "B" * 240
TAIL_MARK = "C" * 8


def test_the_inbox_carries_whole_messages(bus, holder, capsys):
    """`inbox` is the messages in your inbox, the same as the tool of that
    name. Nothing between the sender and the reader decides how much of one
    they get: MAX_TEXT caps a body at 32,768 when it is sent, and that is the
    only limit there is.

        A, no B       -- only the summary came back
        A and B, no C -- the body was truncated
    """
    store_register(SENDER, "other", pid=os.getpid(), home=bus)
    store_register(READER, "other", pid=holder.pid, home=bus)
    main(["send", READER, "-m", BODY_MARK + TAIL_MARK,
          "--summary", SUBJECT_MARK, "--from-name", SENDER])
    capsys.readouterr()

    shown = _out(capsys, ["inbox", "--address", READER])
    assert SUBJECT_MARK in shown, "the summary is the subject line"
    assert BODY_MARK in shown, "only the summary came back"
    assert TAIL_MARK in shown, "the body was truncated"


def test_the_whole_cycle_works_from_the_text_output_alone(delivered, capsys):
    """pi has no MCP tools and works. An agent that has to ask for `--json` and
    parse it is an agent we have handed the implementation to."""
    _, line = delivered
    ident = line.split("id=")[1].split()[0]

    assert BODY.strip() in _out(capsys, ["read", ident, "--address", READER])
    assert _out(capsys, ["ack", ident, "--address", READER]).strip() == "marked read"


# ------------------------------------------------------------- acknowledging


def test_an_ambiguous_reference_is_refused_rather_than_guessed():
    """Two ids sharing a prefix must not resolve to whichever comes first."""
    from agent_bus.store import resolve_message_id

    msgs = [{"id": "abcd1111-x"}, {"id": "abcd2222-y"}]
    with pytest.raises(ValueError, match="matches 2 messages"):
        resolve_message_id(msgs, "abcd")


def test_an_empty_reference_matches_nothing_rather_than_everything():
    from agent_bus.store import resolve_message_id

    assert resolve_message_id([{"id": "abcd1111"}], "") is None


def test_acking_the_same_message_twice_is_not_an_error(delivered, capsys):
    """A reader that re-reads its watch output will ack again. It did."""
    full_id, _ = delivered
    assert _out(capsys, ["ack", full_id[:8], "--address", READER]).strip() == "marked read"
    assert _out(capsys, ["ack", full_id[:8], "--address", READER]).strip() == "marked read"


# ------------------------------------------------------------- the cycle itself


def test_one_pass_of_the_cycle_creates_no_files_in_the_working_tree(
    delivered, capsys, tmp_path, monkeypatch
):
    """The claim that failed in the field: reading your mail leaves nothing
    behind where the user is working."""
    cwd = tmp_path / "worktree"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    full_id, _ = delivered

    _out(capsys, ["inbox", "--address", READER])
    _out(capsys, ["read", full_id[:8], "--address", READER])
    _out(capsys, ["ack", full_id[:8], "--address", READER])

    assert list(cwd.iterdir()) == [], f"left behind: {list(cwd.iterdir())}"
