"""A real coding agent joins the bus and gets a message through.

One shape, for every harness: `register`, claiming a name of its own
choosing. Nothing registers merely by connecting, for any harness, including
omp -- an MCP connection with no `AGENT_BUS_NAME` set in its own worktree
config gets no roster entry at all until it calls `register` itself.

The assertion is a delivered message rather than a roster entry, and that is
deliberate. A headless agent is a one-shot -- it registers, exits, and its entry
is pruned as dead, correctly, because presence is liveness. Asserting it appears
in `list` would be asserting it is still running. Mail outlives its sender, and
the sender recorded on it proves the name and kind the bus actually gave it.
One assertion, both halves. It is also the only honest way to ask what omp is
called: `self` over the CLI resolves by walking the process tree, which from a
headless `--no-session` run climbs out of the test and into whatever session
launched it -- on a developer's machine, their own.

If this fails for one harness only, the fault is almost always in how that
harness is wired rather than in agent-bus: see docs/harnesses/<harness>.md.

A real sequence diagram per harness, captured from this test, is in
test_a_harness_joins_the_bus.md -- including a headless agent's one-shot lifetime,
which is the shape this test cannot show either.
"""

import subprocess

import pytest
from agent_names import mint_agent_name
from busctl import inbox, register, tools_called
from harnesses import HARNESSES
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]


@pytest.mark.parametrize("harness", [pytest.param(h, id=h.name) for h in HARNESSES])
def test_it_joins_and_its_message_arrives_from_the_name_it_claimed(
    project, bus_home, harness
):
    if not harness.available:
        pytest.skip(f"{harness.binary} not on PATH")

    name, target = mint_agent_name(), mint_agent_name()
    prompt = render("join_via_mcp", name=name, kind=harness.kind, target=target)

    # A target that outlives the agent, so the mail has somewhere to land.
    holder = subprocess.Popen(["sleep", "600"])
    cleanup = harness.wire(project, bus_home) if harness.wire else (lambda: None)
    try:
        # After `wire`, because the answer depends on the config it just wrote,
        # and before anything is spent: grok declines to start a repo-local
        # server in an untrusted folder and then improvises the CLI, which
        # makes this row measure the fallback and cost 420s to do it.
        blocked = harness.mcp_preflight() if harness.mcp_preflight else None
        if blocked:
            pytest.skip(f"{harness.name} will not start our MCP server here: {blocked}")

        register(bus_home, target, "other", pid=holder.pid)
        r = harness.run(harness.workdir(project), prompt, home=bus_home)

        msgs = inbox(bus_home, target)
        assert msgs, (
            f"{harness.name} joined the bus but nothing arrived.\n"
            f"exit={r.returncode}\nstdout:\n{r.stdout[-3000:]}\n"
            f"stderr:\n{r.stderr[-1500:]}"
        )
        senders = {(m["from"]["name"], m["from"]["kind"]) for m in msgs}
        assert (name, harness.kind) in senders, (
            f"a message arrived but not from {name!r} as {harness.kind!r}; "
            f"the agent never claimed its identity. senders={senders}\n"
            f"tools called over MCP: {sorted(tools_called())}. A sender "
            f"recorded as 'other' with the right name is `agent-bus send "
            f"--from-name`: an explicit from_name mints a fresh id and keeps "
            f"from_kind's default (store.py), so this is a harness that "
            f"shelled out instead."
        )
        assert {"register", "send_message"} <= tools_called(), (
            f"{harness.name} did not claim its name and send over MCP -- "
            f"tools/call records name {sorted(tools_called())}. Every harness "
            f"here has a shell, so it can improvise `agent-bus` commands and "
            f"leave the assertion above satisfied; this one is what notices. "
            f"An `mcp` surface record alone would not do: the server logs its "
            f"own startup and handshake before the model takes a turn."
        )
    finally:
        cleanup()
        holder.kill()
        holder.wait()
