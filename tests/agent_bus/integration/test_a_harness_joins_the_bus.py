"""A real coding agent joins the bus and gets a message through.

Two shapes, because joining no longer works the same way for all of them.

`register` is what grok and codex do, and what any harness may do to claim a
name of its own choosing. omp needs none of it: the MCP server registers its
connection during `initialize` from `clientInfo`, then renames it from the
client's own `roots/list` answer, so an omp session is named after its project
before it has taken a turn. Both are covered here -- the claimed name, which
every harness can ask for, and omp's derived one, which it gets by existing.

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
from harnesses import BY_NAME, HARNESSES
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


def test_omp_arrives_named_after_its_project_without_ever_registering(
    project, bus_home
):
    """The brief forbids `register`, and a name still shows up on the mail.

    `omp-<project dir>` is not a guess: `_adopt_identity_from_client` derives
    `omp-<pid>` from the handshake, and `_adopt_root` replaces it with the
    basename of the first root the client reports -- this test's own `project`
    fixture. A run that reached the model but skipped either hook sends as
    `pending-<pid>` or `omp-<pid>`, and both fail here by name.
    """
    omp = BY_NAME["omp"]
    if not omp.available:
        pytest.skip(f"{omp.binary} not on PATH")

    target = mint_agent_name()
    prompt = render("send_via_mcp", target=target)

    holder = subprocess.Popen(["sleep", "600"])
    cleanup = omp.wire(project, bus_home) if omp.wire else (lambda: None)
    try:
        register(bus_home, target, "other", pid=holder.pid)
        r = omp.run(omp.workdir(project), prompt, home=bus_home)

        msgs = inbox(bus_home, target)
        assert msgs, (
            "omp sent nothing, so there is no name to check.\n"
            f"exit={r.returncode}\nstdout:\n{r.stdout[-3000:]}\n"
            f"stderr:\n{r.stderr[-1500:]}"
        )
        senders = {(m["from"]["name"], m["from"]["kind"]) for m in msgs}
        assert (f"omp-{project.name}", "omp") in senders, (
            f"omp sent mail as {senders} -- nothing named it after "
            f"{project.name!r}, so one of the two adoption hooks did not run"
        )
        assert tools_called() == {"send_message"}, (
            f"the brief allows exactly one tool call and the log shows "
            f"{sorted(tools_called())}. A `register` here would mean the name "
            f"above was claimed rather than derived, which is the other test."
        )
    finally:
        cleanup()
        holder.kill()
        holder.wait()
