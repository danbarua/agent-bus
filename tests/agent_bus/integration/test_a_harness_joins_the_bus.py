"""A real coding agent joins the bus and gets a message through.

One test, run once per harness. Joining is the part that differs between them
and the part most likely to break: omp, grok and codex start our MCP server and
call `register`; pi has no MCP at all and shells out to the CLI.

The assertion is a delivered message rather than a roster entry, and that is
deliberate. A headless agent is a one-shot -- it registers, exits, and its entry
is pruned as dead, correctly, because presence is liveness. Asserting it appears
in `list` would be asserting it is still running. Mail outlives its sender, and
the sender recorded on it proves the agent claimed the name and kind it said it
would. One assertion, both halves.

If this fails for one harness only, the fault is almost always in how that
harness is wired rather than in agent-bus: see docs/harnesses/<harness>.md.

A real sequence diagram per harness, captured from this test, is in
docs/e2e-scenarios.md -- including a headless agent's one-shot lifetime,
which is the shape this test cannot show either.
"""

import subprocess

import pytest
from agent_names import mint_agent_name
from busctl import CLI, inbox, register
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
    if harness.joins_by == "mcp":
        prompt = render("join_via_mcp", name=name, kind=harness.kind, target=target)
    else:
        # A shell-only peer must pass `--pid $PPID`, which inside its own shell
        # tool is the agent's pid, not the CLI process that exits immediately.
        prompt = render("join_via_shell", cli=CLI, name=name, kind=harness.kind,
                        target=target)

    # A target that outlives the agent, so the mail has somewhere to land.
    holder = subprocess.Popen(["sleep", "600"])
    cleanup = harness.wire(project, bus_home) if harness.wire else (lambda: None)
    try:
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
            f"the agent never claimed its identity. senders={senders}"
        )
    finally:
        cleanup()
        holder.kill()
        holder.wait()
