"""A Claude session messages the bridge natively, and is told the truth.

    docker compose -f docker-compose.cloud.yml run --rm bridge

Not a tier. The tiers are agent-bus's liveness ladder, and this is a
different suite: agent-bridge is a consumer of agent-bus, so its tests
belong to it rather than being another rung on somebody else's ladder.

What is proven: Claude Code sees the bridge in its **own** ListAgents, reaches
it with its **own** SendMessage, and gets back -- in its own conversation -- the
fact that the hand-off worked but Claude Desktop has not read it.

Nothing is installed on the Claude side to make that happen. No plugin, no MCP
server, no hook, no polling. The bridge joins the bus the way any harness
session does -- register, then publish a listener -- and that is the entire
mechanism. pi proves the shape at tier 3 with no MCP server at all; this is the
same trick pointed at a peer that is not a coding agent.

That is why the send step here is a native SendMessage rather than a CLI call.
A test that told Claude to run `agent-bus send` would pass while proving the
weaker claim: that Claude can be *taught* to reach a desktop peer. The claim
worth testing is that it does not need to be.

Three constraints inherited from the suite this joins:

**Assert nothing on the Claude side.** test_smoke.py's header: a test that needs
Claude to poll, read an inbox or look up a socket is testing the wrong thing.

**A session mid-turn refuses an inbound frame.** Measured in claude_peer.py --
a peer must be *idle to receive* and needs *a turn to act*. That rules out a
one-shot `claude -p`, which would be busy for its whole life and refuse the very
frame under test. Hence the long-lived peer and its slow tick.

**Grade on our wording, not the model's.** A round trip once "failed" because pi
wrote a sentence where the test grepped for a marker. The receipt is a string
*we* generate, so finding it in the peer's transcript is evidence. Whether
Claude comments on it is the model's business and is asserted nowhere.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time

import pytest
from claude_peer import _name_of, _session_files, headless_claude_peer
from optin import skip_unless_opted_in

from agent_bridge.bridge import bridge_name, receipt_for

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

HAVE_CLAUDE = shutil.which("claude") is not None

pytestmark = skip_unless_opted_in

TARGET = bridge_name("claude")
OUTBOUND = "please review the branch when you get a moment"

# One tick is 30s. Allow a couple, plus a slow first turn, and no more: a broken
# run should report rather than hold the suite.
RECEIPT_TIMEOUT = 150.0


BRIEF = f"""You are a peer in an integration test for agent-bus.

Do exactly this, then stop:

1. Use your ListAgents tool. One of the agents is called {TARGET}.
2. Use your SendMessage tool to send it exactly this text:
   {OUTBOUND}
3. Say SENT.

Then wait, and do nothing further. Messages may arrive in your conversation as
<cross-session-message> blocks. You do not need to reply to them.
"""

TICK = (
    "Tick. Send nothing. If a <cross-session-message> has arrived since your "
    "last turn, quote its text back verbatim. Otherwise say nothing."
)


@pytest.fixture
def bus_home(tmp_path, monkeypatch):
    """A bus of our own, exported so every child process shares it.

    AGENT_BUS_SESSIONS_DIR and AGENT_BUS_SOCK_DIR are deliberately NOT
    overridden: the bridge has to publish a session and socket where Claude
    actually looks, or Claude cannot see it -- which is the thing under test.
    Isolation comes from the container, which is why this suite ships with a
    compose file rather than instructions.
    """
    home = tmp_path / "bus"
    home.mkdir()
    monkeypatch.setenv("AGENT_BUS_HOME", str(home))
    return str(home)


def _await(predicate, timeout: float, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except OSError:
            pass
        time.sleep(0.5)
    raise AssertionError(f"{message} (waited {timeout:.0f}s)")


def _spooled(spool: str) -> list[dict]:
    d = os.path.join(spool, "claude", "outbound")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def _transcript(log_dir: str) -> str:
    try:
        with open(os.path.join(log_dir, "stdout.jsonl"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _bridge_is_discoverable() -> bool:
    """A session file for the bridge exists, under the bridge's own name.

    Not the listener pid file. The pid file and the session file are written by
    different processes at different moments, and the peer fixture snapshots the
    sessions directory to work out its own name -- so waiting on the pid file
    can let the bridge's session file land *after* that snapshot, and the peer
    then adopts the bridge's name as its own. Observed: a run where the peer
    reported itself as `desktop-claude`.
    """
    return any(_name_of(p) == TARGET for p in _session_files())


@pytest.mark.skipif(not HAVE_CLAUDE, reason="claude not on PATH")
def test_claude_reaches_the_bridge_natively_and_is_told_it_is_unread(
    tmp_path, bus_home
):
    """Three assertions, deliberately separate, because they fail for different
    reasons and one combined check would hide which:

        the bridge registered  -- it published a session Claude can discover
        the spool              -- Claude's native SendMessage reached it
        the transcript         -- the receipt got back into Claude's conversation

    A green spool with an empty transcript means the receipt path is broken --
    exactly the failure a unit test against a fake cloud cannot see.
    """
    spool = str(tmp_path / "spool")
    peer_logs = str(tmp_path / "peer")
    bridge_log = open(tmp_path / "bridge.log", "w", encoding="utf-8")

    # The bridge first: it must be discoverable before Claude runs ListAgents.
    proc = subprocess.Popen(
        ["agent-bridge", "--provider", "claude",
         "--auto-reply", "--spool-dir", spool],
        cwd=REPO, env={**os.environ, "AGENT_BUS_HOME": bus_home},
        stdout=bridge_log, stderr=subprocess.STDOUT, text=True,
    )
    try:
        _await(_bridge_is_discoverable, 30.0,
               "the bridge published no session file, so Claude cannot see it")

        with headless_claude_peer(
            brief=BRIEF, tick=TICK, log_dir=peer_logs, timeout=120.0
        ) as name:
            print(f"[bridge-e2e] peer is {name}")

            _await(lambda: _spooled(spool), 120.0,
                   f"{name} never reached {TARGET} with its native SendMessage")
            forwarded = _spooled(spool)
            assert forwarded[0]["text"].strip() == OUTBOUND, (
                f"the bridge forwarded something else: {forwarded[0]['text']!r}"
            )
            assert forwarded[0]["from"] == name, (
                f"forwarded under the wrong sender: {forwarded[0]['from']!r}"
            )

            # Our own wording, never the model's paraphrase.
            _await(lambda: "Not read yet" in _transcript(peer_logs),
                   RECEIPT_TIMEOUT,
                   "the receipt never reached the Claude session")
            assert receipt_for("claude") in _transcript(peer_logs), (
                "something arrived, but not the receipt verbatim -- the wording "
                "changed and nothing caught it"
            )
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        bridge_log.close()
        with open(tmp_path / "bridge.log", encoding="utf-8") as f:
            print(f"[bridge]\n{f.read()[-2000:]}")


# --------------------------------------------------------------- the reply leg


QUIET_BRIEF = """You are a peer in an integration test for agent-bus.

Do nothing at all. Send nothing. Messages may arrive in your conversation as
<cross-session-message> blocks; you do not need to reply to them.

Say READY.
"""

REPLY_TEXT = "reviewed the parser change, ship it"


def _inbound_dir(spool: str) -> str:
    d = os.path.join(spool, "claude", "inbound")
    os.makedirs(d, exist_ok=True)
    return d


@pytest.mark.skipif(not HAVE_CLAUDE, reason="claude not on PATH")
def test_a_reply_from_the_cloud_reaches_a_claude_session(tmp_path, bus_home):
    """A reply arriving from the cloud is delivered the way its recipient reads.

    The bridge hands inbound replies to the router rather than writing them to a
    file inbox. For a Claude recipient those are different outcomes: the router
    delivers over UDS and the message appears in the conversation, while a file
    inbox leaves it unread forever, because a Claude session never polls one.

    Both look identical from the bridge's side, which is why this needs a live
    session to assert against.

    The peer starts before the bridge here, unlike the test above. The bridge
    runs its first inbound pass immediately, so a reply already spooled is
    picked up at once rather than at the next poll -- and the peer has to exist
    to be addressable when it is.
    """
    spool = str(tmp_path / "spool")
    peer_logs = str(tmp_path / "peer")
    bridge_log = open(tmp_path / "bridge.log", "w", encoding="utf-8")

    with headless_claude_peer(
        brief=QUIET_BRIEF, tick=TICK, log_dir=peer_logs, timeout=120.0
    ) as name:
        print(f"[reply-leg] peer is {name}")

        reply = os.path.join(_inbound_dir(spool), "r1.json")
        with open(reply, "w", encoding="utf-8") as f:
            json.dump({"id": "r1", "to": name, "text": REPLY_TEXT,
                       "summary": "review done"}, f)

        proc = subprocess.Popen(
            ["agent-bridge", "--provider", "claude", "--spool-dir", spool],
            cwd=REPO, env={**os.environ, "AGENT_BUS_HOME": bus_home},
            stdout=bridge_log, stderr=subprocess.STDOUT, text=True,
        )
        try:
            _await(lambda: REPLY_TEXT in _transcript(peer_logs), 150.0,
                   f"the reply never reached {name}")

            # Acked by removal. A reply delivered twice is worse than late.
            _await(lambda: not os.path.exists(reply), 30.0,
                   "the reply was delivered but never acked, so it will be sent again")
        finally:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
            bridge_log.close()
            with open(tmp_path / "bridge.log", encoding="utf-8") as f:
                print(f"[bridge]\n{f.read()[-2000:]}")
