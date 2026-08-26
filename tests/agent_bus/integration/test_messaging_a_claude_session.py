"""A peer messages a live Claude session over UDS, and hears back.

This is the product. A Claude session has no plugin, no MCP server and no inbox
-- it discovers the peer because `listen` writes the session file Claude already
reads, and it answers with its own native SendMessage. Nothing is installed on
that side, so nothing here asserts anything about it or inspects it.

Driven by pi, the least capable harness available: no MCP, no hooks, only a
shell. That is the point. It exercises the CLI path nothing else touches, and it
found a real bug doing so -- `run_listen` published a working socket without
registering under its host pid, so `send` could never locate it. Every other
harness gets its listener from `session_start()` and papers over that path.

Reaching a Claude session needs the sender's *own* listener, because an outbound
frame carries that socket as its reply address. So the peer starts one, and
`SEND_EXIT=0` means all of it worked: the listener came up, published a
Claude-shaped session and socket, registered itself, and the frame landed.

Assertions read marker files the driver's shell wrote, never its narration.
"""

import json

import pytest
from agent_names import mint_agent_name
from busctl import CLI, read_marker
from claude_peer import ACK_TEXT
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

HAVE_PI = BY_NAME["pi"].available


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_a_peer_reaches_a_claude_session(project, bus_home, evidence, claude_session):
    driver = mint_agent_name()
    prompt = render("uds_listen_and_send", cli=CLI, driver=driver, home=bus_home,
                    evidence=evidence, peer=claude_session)
    r = BY_NAME["pi"].run(project, prompt, home=bus_home)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

    sent = read_marker(evidence / "send.txt", "the send step", r)
    assert sent == "SEND_EXIT=0", (
        f"the peer could not message the Claude session over UDS: {sent!r}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_the_claude_sessions_reply_reaches_the_peer(
    project, bus_home, evidence, claude_session
):
    """The peer must stay alive for the round trip: its listener is what the
    reply is addressed to, and a peer that exits takes its socket with it."""
    driver = mint_agent_name()
    prompt = render("uds_listen_send_and_wait", cli=CLI, driver=driver,
                    home=bus_home, evidence=evidence, peer=claude_session)
    r = BY_NAME["pi"].run(project, prompt, home=bus_home, timeout=900)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

    sent = read_marker(evidence / "send.txt", "the send step", r)
    assert sent == "SEND_EXIT=0", (
        f"the peer never reached the Claude session: {sent!r}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )

    # Read out of the driver's own inbox rather than its narration. The reply's
    # wording is briefed, so this also proves it is the peer's answer and not an
    # echo of the outbound message.
    body = read_marker(evidence / "inbox.json", "the inbox poll", r)
    messages = json.loads(body) if body else []
    assert messages, (
        f"no reply arrived from {claude_session} within the wait.\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )
    texts = [m.get("text", "") for m in messages]
    assert any(ACK_TEXT in t for t in texts), (
        f"a message arrived but not the briefed reply {ACK_TEXT!r}: {texts}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )
