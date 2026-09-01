"""`agent-bus self`, run over the CLI by an unregistered peer, tells that peer
something it can act on -- per harness, for every harness we know about.

Mostly a **capture**. The only thing asserted is that the output names a real
`agent-bus` command to run next; the wording is deliberately not pinned,
because the wording is what #140 and #182 are still deciding and a test written
today would freeze whatever we happen to print.

The gap: `test_self_reflects_a_status_it_just_set.py` drives `self` as an MCP
tool, from one harness (codex), that has already registered. Nothing had ever
run `self` over the **CLI**, from an **unregistered** session, on more than one
harness -- which is the state every harness is actually in before anything
tells it to register, and the state #125 rewrote the message for.

What makes the matrix worth capturing rather than obvious: `self` answers by
two different routes and the harnesses do not sit in one bucket.

  omp        genuinely discoverable: omp's own daemon writes
             ~/.omp/run/daemons/*/clients/*.json for its own purposes, so an
             unregistered omp session is findable whether or not anything of
             ours ever ran
  grok       has an adapter and is NOT discoverable. Measured, not assumed --
             this test's own first capture is what established it. The adapter
             reads ~/.grok/active_sessions.json, which is `[]` while a grok
             session is live, so discovery contributes nothing and everything
             grok gets on the bus comes from registering through the MCP server
  codex      no adapter, deliberately (adapters/discovery/__init__.py: it
             records no pid anywhere, so nothing process-shaped can find it)
  pi         shell only, kind `other`, nothing publishes it at all

So ONE reports itself addressable and three report that nothing can address
them. That asymmetry is the point. omp and grok are both `joins_by="mcp"` in
harnesses.py and look interchangeable there; they are not. One is
discovery-shaped, the other is registration-shaped wearing discovery's
clothes, and reading the adapter list would have told you both were covered.

This docstring claimed exactly that until this test's first capture falsified
it, which is the strongest argument for the test existing.

**Read the captures from the container, not a developer machine.** Discovery
reads each harness's real registry, not this test's isolated `AGENT_BUS_HOME`,
so on a host running your own omp or grok sessions an unregistered driver can
resolve to *those* instead of itself -- the same trap #125 named for pytest's
own ancestor chain. `docker compose run --rm e2e` is where this matrix means
what it says.

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


@pytest.mark.parametrize("harness", [pytest.param(h, id=h.name) for h in HARNESSES])
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
