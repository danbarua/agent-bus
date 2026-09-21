"""A real MCP-driven agent identifies itself, lists the roster, reads its own
inbox and acks a message in it -- four of the MCP tools #171 found had never
been called by a real harness.

#171 counted 2 of 8 MCP tools covered (`register`, `send_message`, both via
`join_via_mcp.md`) and named `get_inbox` / `ack_message` / `list_agents` as
the Tier 1 gap among the other six. `read_message` was left out of that list
deliberately (per #171 itself): `get_inbox` already returns each message whole,
so a driver reading its own inbox has no need to also call `read_message` --
it exists for a narrower case (following up on an id a watch line or notice
gave you), which is `get_inbox`'s job to name, not this test's to force a call
to.

Driven by `omp`, because mail reaches a session by name only for a kind whose
mail goes through the file bus: `send` refuses a codex-kind name ("is a codex
process, not a thread"), so a codex driver has no mailbox to read.

`get_inbox` and `ack_message` answer only for the calling session, so the
driver has to own the mailbox. The test registers the driver's name under a
process that then exits, with the message queued: a dead entry that holds unread
mail is kept, and the omp MCP server's `AGENT_BUS_NAME` registration under the
same name and kind takes it over with the same id and the mail. The driver's
`SELF=` line proves the name was taken over and not suffixed `-2`.

There is no shell here to write marker files to (`join_via_mcp.md`'s own
precedent), so the calls are checked by asking the model to relay one strict,
single-token line per step -- `SELF=`, `SEEN=`, `TEXT=`, `ACKED=` -- never
free prose. The tools the session called are read from this test's own log.
Once omp exits, its entry holds no unread mail and is pruned, so the inbox file
cannot be read afterwards.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is in test_mcp_inbox_and_ack_close_the_loop.md.
"""

import re
import subprocess

import pytest
from agent_names import mint_agent_name
from busctl import bus, register, tools_called
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

BODY = "the ordinary loop over MCP: list, read, ack"

OMP = BY_NAME["omp"]


def _line(pattern, stdout):
    """The first group of `pattern` on a line of the model's reply. omp's
    readable output marks the first line of a reply with `[said] `."""
    m = re.search(r"^(?:\[said\] )?" + pattern.removeprefix("^"), stdout, re.MULTILINE)
    return m.group(1) if m else None


@pytest.mark.skipif(not OMP.available, reason="omp not on PATH")
def test_a_driver_lists_reads_and_acks_its_own_mail(project, bus_home, monkeypatch):
    sender, driver = mint_agent_name(), mint_agent_name()
    holder = subprocess.Popen(["sleep", "600"])
    try:
        register(bus_home, driver, "other", pid=holder.pid)
        register(bus_home, sender, "other")
        r = bus(bus_home, "send", driver, "-m", BODY, "--from-name", sender)
        assert r.returncode == 0, f"setup send failed: {r.stderr}"
    finally:
        holder.kill()
        holder.wait()

    # The MCP server registers this name when it starts, and its environment
    # is built from the test's own `AGENT_BUS_*` variables.
    monkeypatch.setenv("AGENT_BUS_NAME", driver)
    assert OMP.wire is not None
    OMP.wire(project, bus_home)

    prompt = render("mcp_inbox_and_ack", sender=sender)
    r = OMP.run(project, prompt, home=bus_home)
    assert r.returncode == 0, f"omp exited {r.returncode}: {r.stderr[-1500:]}"

    self_name = _line(r"^SELF=(.*)$", r.stdout)
    assert self_name == driver, (
        f"the server's AGENT_BUS_NAME registration should have taken over the "
        f"dead entry holding the mail: SELF={self_name!r}\n"
        f"omp stdout:\n{r.stdout[-2500:]}"
    )

    seen = _line(r"^SEEN=(yes|no)\s*$", r.stdout)
    assert seen == "yes", (
        f"list_agents should have listed {sender}: SEEN={seen!r}\n"
        f"omp stdout:\n{r.stdout[-2500:]}"
    )

    text = _line(r"^TEXT=(.*)$", r.stdout)
    assert text == BODY, (
        f"get_inbox should have returned the queued message whole: "
        f"TEXT={text!r}\nomp stdout:\n{r.stdout[-2500:]}"
    )

    acked = _line(r"^ACKED=(yes|no)\s*$", r.stdout)
    assert acked == "yes", (
        f"ack_message reported ACKED={acked!r}\n"
        f"omp stdout:\n{r.stdout[-2500:]}"
    )

    wanted = {"self", "list_agents", "get_inbox", "ack_message"}
    assert wanted <= tools_called(), (
        f"the log should record {sorted(wanted)} being called over MCP, "
        f"got {sorted(tools_called())}"
    )
