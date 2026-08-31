"""Two agents alternating over the bus until one of them says stop.

Every other messaging test is one-shot: a driver sends, a peer replies, the
assertion reads the reply. This one is the first that needs a peer to answer,
go quiet, be spoken to again, and answer again -- seven messages, each turn
started by the message before it.

    A says 1   B says 2   A says 3   B says 4   A says 5   B says DONE   A says ACK

Nothing ticks either peer. Each arms `agent-bus watch` under its own monitor
and stops; a watch line becomes an event, and the event starts the next turn.
That makes the whole exchange about as long as seven model turns rather than
as long as a babysitting loop -- see `mail_woken_peer.py` for the mechanism.

The counters are the assertion. Each side's inbox must hold exactly the values
the other side was supposed to send, in order, so a peer that answers twice, or
answers the wrong number, or stops early, fails on our wording rather than on
anything a model said about itself.

A real sequence diagram from this test -- and the strongest warning in
docs/e2e-scenarios.md against reading it as how a real conversation should
look -- is there. Seven scripted turns and a hardcoded stop word are what CI
needs from a deterministic assertion; they are not a model for how a working
agent should spend its time.
"""

import time

import pytest
from agent_names import mint_agent_name
from busctl import CLI, bus_env, inbox, register
from mail_woken_peer import WAKE, mail_woken_peer
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

LAST = 5
A_EXPECTS = ["2", "4", "DONE"]
B_EXPECTS = ["1", "3", "5", "ACK"]

# Seven model turns, each a shell command and a short reply. Generous rather
# than tuned: the failure worth reporting is "the conversation stalled", and a
# deadline that fires mid-exchange cannot tell that from a slow model.
CONVERSATION_TIMEOUT = 600.0
POLL = 8.0


def _brief(me, peer, harness, *, first):
    """The brief for this harness's wake style, not for this harness.

    A pushed peer ends its turn and is re-invoked, so it is told to stop and
    wait. A parked one blocks in a tool call, so it is told to loop on a cursor.
    Two prompts rather than three: a new push harness needs no new prompt.
    """
    if WAKE[harness] == "park":
        opener = ("2. Now SEND the value 1, before reading any output."
                  if first else "2. Nothing to send yet.")
        return render("conversation_peer_park", me=me, peer=peer, cli=CLI,
                      last=str(LAST), opener=opener, watch=f"buswatch-{me}")
    opener = ("3. Now SEND the value 1. This is the only send you make without"
              " an event." if first else "3. Nothing to send yet.")
    return render("conversation_peer", me=me, peer=peer, cli=CLI,
                  last=str(LAST), opener=opener)


# The pairs worth paying for: one harness talking to itself, and two different
# harnesses talking to each other. The mixed pair is the claim -- it says the
# conversation is a property of the bus rather than of one vendor's tooling.
PAIRS = [("claude", "claude"), ("claude", "grok"), ("claude", "omp")]


@pytest.mark.parametrize(
    ("harness_a", "harness_b"),
    [pytest.param(x, y, id=f"{x}-to-{y}") for x, y in PAIRS],
)
def test_they_alternate_until_one_says_done(bus_home, tmp_path, harness_a, harness_b):
    a, b = mint_agent_name(), mint_agent_name()
    env = bus_env(bus_home)

    # B first, and only then A. `watch` starts from the end of the inbox, so
    # A's opening message would be invisible to a B that is not yet watching --
    # the conversation would never start, and the failure would look like a
    # broken wake rather than a race in the fixture.
    def joins(name):
        # Runs between spawn and brief: watch cannot resolve an inbox for a
        # name that is not on the bus yet.
        return lambda pid: register(bus_home, name, "other", pid=pid)

    with mail_woken_peer(
        b, _brief(b, a, harness_b, first=False),
        harness=harness_b, env=env, cwd=str(tmp_path),
        log_dir=str(tmp_path / f"peer-{b}"), on_spawn=joins(b),
    ) as pb, mail_woken_peer(
        a, _brief(a, b, harness_a, first=True),
        harness=harness_a, env=env, cwd=str(tmp_path),
        log_dir=str(tmp_path / f"peer-{a}"), on_spawn=joins(a),
    ) as pa:
        deadline = time.time() + CONVERSATION_TIMEOUT
        got_a, got_b = [], []
        while time.time() < deadline:
            got_a = [m.get("summary") for m in inbox(bus_home, a)]
            got_b = [m.get("summary") for m in inbox(bus_home, b)]
            print(f"[conversation] {a}={got_a} {b}={got_b}", flush=True)
            if got_a == A_EXPECTS and got_b == B_EXPECTS:
                break
            for name, proc in ((a, pa), (b, pb)):
                assert proc.poll() is None, (
                    f"{name} exited mid-conversation (rc={proc.returncode}); "
                    f"transcripts under {tmp_path}"
                )
            time.sleep(POLL)

    assert got_b == B_EXPECTS, (
        f"{b} should have received {B_EXPECTS}, got {got_b}. "
        f"{a} received {got_a}. Transcripts under {tmp_path}."
    )
    assert got_a == A_EXPECTS, (
        f"{a} should have received {A_EXPECTS}, got {got_a}. "
        f"{b} received {got_b}. Transcripts under {tmp_path}."
    )

