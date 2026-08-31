"""A real MCP-driven agent lists the roster, reads another agent's inbox by
name, and acks a message in it -- three of the six MCP tools #171 found had
never been called by a real harness.

#171 counted 2 of 8 MCP tools covered (`register`, `send_message`, both via
`join_via_mcp.md`) and named `get_inbox` / `ack_message` / `list_agents` as
the Tier 1 gap among the other six. `read_message` was left out of that list
deliberately (per #171 itself); its CLI sibling `read`/`read_one` already got
a live regression guard in #175's `test_read_and_ack_close_the_loop.py`, and
`get_inbox` already returns each message whole, so a driver reading its own
inbox has no need to also call `read_message` -- it exists for a narrower
case (following up on an id a watch line or notice gave you), which is
`get_inbox`'s job to name, not this test's to force a call to.

Driven by `codex`: the one MCP-joining harness that needs no project wiring
and no repo-trust step, keeping this test to the mechanism under test rather
than to codex-the-harness.

The driver deliberately never calls `register`. `get_inbox` and
`ack_message` both take an explicit `name` argument precisely so a caller can
act on a mailbox that is not its own -- the codebase's own docstrings call
this "acking is bookkeeping, not agreement to act," which presumes a caller
other than the message's owner. Checked before writing this, not assumed:
`store.register()` silently auto-renames a caller to `name-2` on any
collision with a live entry under a different pid, so a driver that
registered as {{driver}} to "become" the mailbox pre-seeded under that name
would not become it -- it would collide and land somewhere else, and the
test would either fail confusingly or silently read an empty inbox. Using
`name=` instead of colliding on identity sidesteps that entirely, and is
also the more realistic shape: an operator, bridge, or triage agent
inspecting a named peer's mail, not that peer itself.

There is no shell here to write marker files to (`join_via_mcp.md`'s own
precedent), so the read-only calls are checked by asking the model to relay
one strict, single-token line per step -- `SEEN=`, `TEXT=`, `ACKED=` -- never
free prose. `ack_message`'s result is also checked the way #175's CLI test
checked `ack`: independently, against the real inbox file, because a mutation
proves itself where a read-only call has to be taken on the model's word for
what it saw.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is in test_mcp_inbox_and_ack_close_the_loop.md.
"""

import re
import subprocess

import pytest
from agent_names import mint_agent_name
from busctl import bus, inbox, register
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

BODY = "the ordinary loop over MCP: list, read, ack"

CODEX = BY_NAME["codex"]


def _line(pattern, stdout):
    m = re.search(pattern, stdout, re.MULTILINE)
    return m.group(1) if m else None


@pytest.mark.skipif(not CODEX.available, reason="codex not on PATH")
def test_a_driver_lists_reads_and_acks_a_named_peers_mail(project, bus_home):
    sender, driver = mint_agent_name(), mint_agent_name()
    holder = subprocess.Popen(["sleep", "600"])
    try:
        register(bus_home, driver, "other", pid=holder.pid)
        register(bus_home, sender, "other")
        r = bus(bus_home, "send", driver, "-m", BODY, "--from-name", sender)
        assert r.returncode == 0, f"setup send failed: {r.stderr}"

        prompt = render("mcp_inbox_and_ack", driver=driver)
        r = CODEX.run(CODEX.workdir(project), prompt, home=bus_home)
        assert r.returncode == 0, f"codex exited {r.returncode}: {r.stderr[-1500:]}"

        seen = _line(r"^SEEN=(yes|no)\s*$", r.stdout)
        assert seen == "yes", (
            f"list_agents should have listed {driver}: SEEN={seen!r}\n"
            f"codex stdout:\n{r.stdout[-2500:]}"
        )

        text = _line(r"^TEXT=(.*)$", r.stdout)
        assert text == BODY, (
            f"get_inbox(name={driver!r}) should have returned the seeded "
            f"message whole: TEXT={text!r}\ncodex stdout:\n{r.stdout[-2500:]}"
        )

        acked = _line(r"^ACKED=(yes|no)\s*$", r.stdout)
        assert acked == "yes", (
            f"ack_message reported ACKED={acked!r}\n"
            f"codex stdout:\n{r.stdout[-2500:]}"
        )

        # The authoritative check: not what codex said happened, but what the
        # real inbox file now says.
        msgs = inbox(bus_home, driver)
        assert len(msgs) == 1 and msgs[0]["read"] is True, (
            f"ack_message reported success but the message is still unread: {msgs}"
        )
    finally:
        holder.kill()
        holder.wait()
