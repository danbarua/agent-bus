"""A real shell-only agent receives a message, reads it whole, and acks it --
the ordinary loop, driven end to end for the first time.

#171 named `ack` and `read` as a Tier 1 gap and flagged exactly how it hides:
grepping the existing prompts for "ACK" returns four hits, and every one of
them is the conversation stop-word in conversation_peer.md /
conversation_peer_park.md -- a peer's SendMessage reply, never a call to
`agent-bus ack`. That is the false positive #171 warned would trip up anyone
re-running the audit; this test is what actually exercises the real verb.

Every unit and CLI test for `ack`/`read_one` (test_store.py, test_cli.py,
test_the_watch_cycle.py) calls them directly or against a controlled pid --
never against a real inbox as read by a real shell process deciding what to
do with what it finds there. That gap is also what let a real bug through
unit-tested and unnoticed: `commands/messages.py::ack` was the one verb in
that module missing `@logged` -- every sibling (`send`, `inbox`, `read_one`)
carries it, so `ack` never appeared in the structured log, and therefore
never counted as covered by `scripts/e2e_coverage.py` either, which reads
that same log. Fixed alongside this test, with its own unit regression guard
in test_log.py -- and the fix is also what makes `ack` show up in this
test's own capture below.

Setup is Python driving the CLI directly (`busctl.register`/`bus`, matching
#171's note that the only `bus(home, ...)` calls in this whole directory are
setup, never the verb under test): a sender and the driver register, and the
sender sends one message, so the driver's inbox is non-empty before `pi`
starts. From there, `pi` -- a real shell process, no MCP, nothing installed
on its side -- reads its own inbox, extracts the message id itself, reads
the message whole, acks it, then re-reads its inbox to prove the ack stuck.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is in test_read_and_ack_close_the_loop.md.
"""

import json

import pytest
from agent_names import mint_agent_name
from busctl import CLI, bus, read_marker, register
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

BODY = "the ordinary loop: receive, read, ack"

HAVE_PI = BY_NAME["pi"].available


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_a_shell_peer_reads_and_acks_its_own_mail(project, bus_home, evidence):
    sender, driver = mint_agent_name(), mint_agent_name()
    register(bus_home, sender, "other")
    register(bus_home, driver, "other")
    r = bus(bus_home, "send", driver, "-m", BODY, "--from-name", sender)
    assert r.returncode == 0, f"setup send failed: {r.stderr}"

    prompt = render("read_and_ack", cli=CLI, driver=driver, evidence=evidence)
    r = BY_NAME["pi"].run(project, prompt, home=bus_home)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

    inbox_before = json.loads(read_marker(evidence / "inbox.json", "the inbox step", r))
    assert len(inbox_before) == 1, (
        f"setup should have left exactly one message for {driver}: {inbox_before}"
    )
    assert inbox_before[0]["read"] is False, inbox_before

    read = json.loads(read_marker(evidence / "read.json", "the read step", r))
    assert read["text"] == BODY, (
        f"`read` returned a different body than what was sent: {read!r}"
    )
    assert read["from"]["name"] == sender, read

    acked = json.loads(read_marker(evidence / "ack.json", "the ack step", r))
    assert acked == {"acked": True}, (
        f"`ack` reported {acked!r} for a message `read` had just found: "
        f"pi stdout:\n{r.stdout[-2500:]}"
    )

    inbox_after = json.loads(read_marker(evidence / "inbox_after.json", "the ack step", r))
    assert len(inbox_after) == 1 and inbox_after[0]["read"] is True, (
        f"`ack` reported success but the message is still unread: {inbox_after}"
    )
