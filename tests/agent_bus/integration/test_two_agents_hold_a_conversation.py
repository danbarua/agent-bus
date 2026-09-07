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

Codex is a third mechanism, not a variant of the other two (#292): nothing on
its side watches for mail at all, so it neither arms a monitor nor blocks in
a tool call. The counterpart's own `agent-bus send` writes straight into
codex's queue, and an app-server holding that thread open picks up the write
on its own, idle or busy. See `codex_peer.py`.

The counters are the assertion. Each side's inbox must hold exactly the values
the other side was supposed to send, in order, so a peer that answers twice, or
answers the wrong number, or stops early, fails on our wording rather than on
anything a model said about itself.

A real sequence diagram from this test -- and the strongest warning in
test_two_agents_hold_a_conversation.md against reading it as how a real conversation should
look -- is there. Seven scripted turns and a hardcoded stop word are what CI
needs from a deterministic assertion; they are not a model for how a working
agent should spend its time.
"""

import time

import pytest
from agent_names import mint_agent_name
from busctl import CLI, bus_env, inbox, register
from codex_peer import CodexPeerHandle, codex_peer
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
    wait. A parked one blocks in a tool call, so it is told to loop on a
    cursor. Codex watches nothing at all, so it is told neither -- every
    message it is given after the brief already IS the next event.
    """
    style = WAKE[harness]
    if style == "park":
        opener = ("2. Now SEND the value 1, before reading any output."
                  if first else "2. Nothing to send yet.")
        return render("conversation_peer_park", me=me, peer=peer, cli=CLI,
                      last=str(LAST), opener=opener, watch=f"buswatch-{me}")
    if style == "queue":
        opener = ("Now SEND the value 1." if first else
                  "Wait for your partner's first message; there is nothing "
                  "to send yet.")
        return render("conversation_peer_codex", me=me, peer=peer, cli=CLI,
                      last=str(LAST), opener=opener)
    opener = ("3. Now SEND the value 1. This is the only send you make without"
              " an event." if first else "3. Nothing to send yet.")
    return render("conversation_peer", me=me, peer=peer, cli=CLI,
                  last=str(LAST), opener=opener)


# The pairs worth paying for: one harness talking to itself, two different
# harnesses talking to each other, and codex -- whose wake mechanism is a
# third thing entirely, never a roster entry, addressed by thread id rather
# than name (#292). Codex only appears as the second harness: it is the one
# whose "peer" address isn't known until its own context manager has already
# started the thread, so the test body constructs it in a different order.
# Codex-as-first-speaker would need that same restructuring a second time and
# is left for a follow-up rather than doubling this PR's scope.
PAIRS = [("claude", "claude"), ("claude", "grok"), ("claude", "omp"),
         ("claude", "codex")]


@pytest.mark.parametrize(
    ("harness_a", "harness_b"),
    [pytest.param(x, y, id=f"{x}-to-{y}") for x, y in PAIRS],
)
def test_they_alternate_until_one_says_done(bus_home, tmp_path, harness_a, harness_b):
    a, b = mint_agent_name(), mint_agent_name()
    env = bus_env(bus_home)
    codex_b = harness_b == "codex"

    # B first, and only then A. `watch` starts from the end of the inbox, so
    # A's opening message would be invisible to a B that is not yet watching --
    # the conversation would never start, and the failure would look like a
    # broken wake rather than a race in the fixture. Codex's own gate is
    # different (a thread with no completed turn has no rollout yet, so a
    # queue write against one fails hard) but the ordering requirement is the
    # same: B must be ready before A's brief can name it as `{{peer}}`.
    def joins(name):
        # Runs between spawn and brief: watch cannot resolve an inbox for a
        # name that is not on the bus yet.
        return lambda pid: register(bus_home, name, "other", pid=pid)

    b_ctx = (
        codex_peer(_brief(b, a, harness_b, first=False),
                  env=env, log_dir=str(tmp_path / f"peer-{b}"))
        if codex_b else
        mail_woken_peer(
            b, _brief(b, a, harness_b, first=False),
            harness=harness_b, env=env, cwd=str(tmp_path),
            log_dir=str(tmp_path / f"peer-{b}"), on_spawn=joins(b),
        )
    )

    with b_ctx as pb:
        # A codex peer is addressed by its thread id, never a roster name --
        # it never registers, so `b` (the minted name) names nothing on the
        # bus for A to send to. isinstance, not codex_b, is what narrows pb's
        # type here -- the two always agree, since b_ctx picked pb's type
        # from the same condition.
        b_address = pb.thread_id if isinstance(pb, CodexPeerHandle) else b

        with mail_woken_peer(
            a, _brief(a, b_address, harness_a, first=True),
            harness=harness_a, env=env, cwd=str(tmp_path),
            log_dir=str(tmp_path / f"peer-{a}"), on_spawn=joins(a),
        ) as pa:
            deadline = time.time() + CONVERSATION_TIMEOUT
            got_a, got_b = [], []
            while time.time() < deadline:
                got_a = [m.get("summary") for m in inbox(bus_home, a)]
                # Codex has no file-bus inbox -- it is never a roster entry,
                # and its replies arrive at A's inbox instead. A's inbox
                # matching A_EXPECTS is the proof codex received and acted
                # correctly on every message; the structural check after the
                # loop covers what that alone can't (the final ACK, which
                # codex is told to answer with silence).
                got_b = [] if codex_b else [m.get("summary") for m in inbox(bus_home, b)]
                print(f"[conversation] {a}={got_a} {b}={got_b}", flush=True)
                if got_a == A_EXPECTS and (codex_b or got_b == B_EXPECTS):
                    break
                for name, proc in ((a, pa), (b, pb)):
                    assert proc.poll() is None, (
                        f"{name} exited mid-conversation (rc={proc.returncode}); "
                        f"transcripts under {tmp_path}"
                    )
                time.sleep(POLL)

        if not codex_b:
            assert got_b == B_EXPECTS, (
                f"{b} should have received {B_EXPECTS}, got {got_b}. "
                f"{a} received {got_a}. Transcripts under {tmp_path}."
            )
        assert got_a == A_EXPECTS, (
            f"{a} should have received {A_EXPECTS}, got {got_a}. "
            f"{b} received {got_b}. Transcripts under {tmp_path}."
        )

        if isinstance(pb, CodexPeerHandle):
            # The strong assertion inbox alone can't make: codex's own turns
            # received exactly B_EXPECTS as input, in order -- not just that
            # its replies happened to look right. The first userMessage item
            # is the opening brief, not a conversation value.
            thread = pb.server.resume_thread(pb.thread_id)
            user_texts = [
                item["content"][0]["text"]
                for turn in thread.get("turns", [])
                for item in turn.get("items", [])
                if item.get("type") == "userMessage"
            ][1:]
            assert user_texts == B_EXPECTS, (
                f"codex thread {pb.thread_id} should have received "
                f"{B_EXPECTS} as turn inputs, got {user_texts}. "
                f"Transcripts under {tmp_path}."
            )

