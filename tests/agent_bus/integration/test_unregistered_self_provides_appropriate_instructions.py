"""`agent-bus self`, run over the CLI by an unregistered peer, tells that peer
something it can act on -- per harness, for every harness that has a reason to
type the command.

Mostly a **capture**. The only thing asserted is that the output names a real
`agent-bus` command to run next; the wording is deliberately not pinned,
because the wording is what #140 and #182 are still deciding and a test written
today would freeze whatever we happen to print.

It is also where `cli`-surface verb coverage comes from: scripts/e2e_coverage.py
reads the per-test logs, so a harness that never types a command has no `cli`
row in the generated matrix at all.

The gap it was written for: `test_self_reflects_a_status_it_just_set.py` drives
`self` as an MCP tool, from one harness (codex), that has already registered.
Nothing had ever run `self` over the **CLI**, from an **unregistered** session,
on more than one harness -- the state a harness is in before anything tells it
to register, and the state #125 rewrote the message for.

**omp is not in this matrix, and cannot be.** It never types a command about
its own identity: the MCP server registers its connection during `initialize`
and renames it from the client's own `roots/list` answer, so "an unregistered
omp session" stopped existing once our server is wired. Driving the CLI from
one anyway answers about a different session entirely -- captured, from an omp
whose own roster row read `omp-proj` at pid 1169:

    not registered -- but reachable as omp-agent-bus (omp), discovered by
    your harness. Peers can send to you already.

`omp-agent-bus` was the *developer's* session, three processes up. A headless
run passes `--no-session`, so it publishes no session file, so omp's own
adapter cannot see it, so `resolve_host_pid` keeps walking the ancestor chain
-- and an agent developing agent-bus is itself on agent-bus, so the chain ends
at a real registered omp. What omp does instead is covered over MCP, where its
identity is, by `test_a_harness_joins_the_bus.py`.

What makes the rest worth capturing rather than obvious: neither remaining
harness is discoverable, but for different reasons -- grok's adapter was
removed (#184) and codex never had one (adapters/discovery/__init__.py: it
records no pid anywhere, so nothing process-shaped can find it). Both should
report that nothing can address them, which is the branch whose advice has to
be actionable, since `join` is for a peer with no socket of its own.

(The `pi` harness -- shell only, kind `other`, nothing publishes it at all --
used to give this matrix its third case. It was retired with the rest of the
`pi` fixture.)

**Read the captures from the container, not a developer machine.** Discovery
reads each harness's real registry, not this test's isolated `AGENT_BUS_HOME`,
so on a host running your own sessions an unregistered driver can resolve to
*those* instead of itself -- the same trap #125 named for pytest's own ancestor
chain, and the one the omp row above walked into. `docker compose run --rm e2e`
is where this matrix means what it says.

Evidence lands in `.e2e/<test id>/evidence/`: `self.out`, `self.err`,
`self.exit`. `self` exits 1 when unregistered; that is a recorded outcome
here, not a failure.
"""

import re

import pytest
from busctl import CLI
from harnesses import HARNESSES
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

#: Any runnable next step. Not the sentence around it -- that is #140's to settle.
NAMES_A_COMMAND = re.compile(r"agent-bus (register|join|listen)\b")


#: omp is absent by design -- see the module docstring. Registered by our own
#: MCP server before it takes a turn, it has no unregistered state to capture.
CLI_HARNESSES = [h for h in HARNESSES if h.name != "omp"]


@pytest.mark.parametrize("harness", [pytest.param(h, id=h.name) for h in CLI_HARNESSES])
def test_unregistered_self_names_a_command_to_run(project, bus_home, evidence, harness):
    if not harness.available:
        pytest.skip(f"{harness.binary} not on PATH")

    prompt = render("cli_self_unregistered", cli=CLI, evidence=evidence)
    cleanup = harness.wire(project, bus_home) if harness.wire else (lambda: None)
    try:
        r = harness.run(harness.workdir(project), prompt, home=bus_home)
    finally:
        cleanup()

    marker = evidence / "self.exit"
    assert marker.exists(), (
        f"{harness.name} never ran `agent-bus self`: self.exit was not written, "
        f"so there is nothing to capture.\n"
        f"exit={r.returncode}\nstdout:\n{r.stdout[-2500:]}"
    )
    out = (evidence / "self.out").read_text().strip()
    err = (evidence / "self.err").read_text().strip()

    # Surfaced under `pytest -s` so the matrix can be read without digging
    # through .e2e/. This is the point of the test.
    print(f"\n=== {harness.name} ({harness.kind}, joins_by={harness.joins_by}) "
          f"{marker.read_text().strip()} ===")
    for stream, text in (("out", out), ("err", err)):
        for line in text.splitlines():
            print(f"  {stream}| {line}")

    said = f"{out}\n{err}"
    assert NAMES_A_COMMAND.search(said), (
        f"`agent-bus self` told {harness.name} it was unregistered without "
        f"naming a command it could run about it. An unactionable answer is "
        f"the thing #125 fixed for one case and this checks for all of them.\n"
        f"self said:\n{said[:1200]}"
    )
