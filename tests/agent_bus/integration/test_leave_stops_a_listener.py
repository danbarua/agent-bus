"""`leave` stops the listener it unregisters, rather than only forgetting it.

`leave` reports one thing and does two: it drops the roster entry AND stops the
published listener. Nothing drove it from a real shell, so only the first half
was ever observed -- and the two can disagree. A `leave` that unregisters a name
while its process keeps the socket bound reports success and leaves a listener
nobody can address and nobody knows is there.

The path here is the one where they come apart. `listen --pid $PPID` with no
prior `register` makes the roster entry carry the LISTENER's pid, while
`stop_uds_listen` is keyed on the HOST pid -- so a lookup by the roster's pid
finds no pid file, stops nothing, and the unregister proceeds regardless.

Driven by pi deliberately: shell only, no MCP, no hooks. `leave` is a shell verb
and this is the surface a person or an agent actually types it on. The unit
tests in tests/agent_bus/test_cli.py cover the same verb with a controlled pid,
which is exactly the case where the two pids agree.

A real sequence diagram from this test is in docs/e2e-scenarios.md.
"""

import json

import pytest
from agent_names import mint_agent_name
from busctl import CLI, read_marker
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]


@pytest.mark.skipif(not BY_NAME["pi"].available, reason="pi not on PATH")
def test_leave_stops_the_listener_it_unregisters(project, bus_home, evidence):
    driver = mint_agent_name()

    prompt = render("listen_then_leave", cli=CLI, driver=driver,
                    home=bus_home, evidence=evidence)
    r = BY_NAME["pi"].run(project, prompt, home=bus_home)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

    read_marker(evidence / "listener.pid", "the listen step", r)
    before = json.loads(read_marker(evidence / "before.json", "the listen step", r))
    assert driver in [a["name"] for a in before], (
        f"{driver} never joined, so there is nothing for `leave` to stop: "
        f"{[a['name'] for a in before]}"
    )

    # `leave` is allowed to report either outcome honestly. What it may not do
    # is report success while the process it claims to have stopped is running.
    left = json.loads(read_marker(evidence / "leave.json", "the leave step", r))

    after_proc = read_marker(evidence / "after.txt", "the leave step", r).strip()
    assert after_proc == "STOPPED", (
        f"`agent-bus leave --name {driver}` returned {left!r} and the listener "
        f"is {after_proc}. Unregistering a name whose process still holds its "
        f"socket leaves a peer that is bound, unaddressable, and invisible to "
        f"the roster that would otherwise have found it."
    )

    after = json.loads(read_marker(evidence / "after.json", "the leave step", r))
    assert driver not in [a["name"] for a in after], (
        f"{driver} is still listed after `leave` reported {left!r}: "
        f"{[a['name'] for a in after]}"
    )
