"""`join` publishes a listener that is actually reachable the instant it
returns, with no sleep or backgrounding needed to close the gap.

#171 named `join` as the sibling gap to #170's `leave` fix: the newest verbs,
the only two a real harness had never touched, and `join` is the one that
spawns the detached listener in the first place -- the same class of process
this session found a real pid bug in, on `leave`'s side.

Live-reproduced by hand before writing this: `join` from a real shell,
against a real held pid, correctly resolved the host pid, published a
listener whose pid file matched, reported `reachable: true`, and both an
inbound send to it and an outbound send *from* it (this test's own risk)
worked -- no divergence and no bug, unlike `leave`. `join`'s actual,
documented risk is narrower and different: `register()` claims a name and
stops; the listener that gives a peer a socket to send *from* is a detached
process, so there is a window between "registered" and "can send" where an
agent that starts working loses whatever it tries to send. `join` closes
that window by waiting for the socket to exist before returning -- this
test is whether that wait actually holds, for real, driven by a shell.

`test_messaging_a_claude_session.py` is the template: same mechanism (a
shell-only peer reaching a live Claude session over UDS), replacing that
test's `listen --pid $PPID & sleep 6` with a single blocking `join` call and
no sleep at all -- `join`'s own wait is the thing under test, so nothing
here may paper over a race the same way `listen`'s callers otherwise do.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is in docs/e2e-scenarios.md.
"""

import pytest
from agent_names import mint_agent_name
from busctl import CLI, read_marker
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

HAVE_PI = BY_NAME["pi"].available


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_join_is_reachable_the_instant_it_returns(project, bus_home, evidence, claude_session):
    driver = mint_agent_name()
    prompt = render("join_and_send", cli=CLI, driver=driver,
                    evidence=evidence, peer=claude_session)
    r = BY_NAME["pi"].run(project, prompt, home=bus_home)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

    joined = read_marker(evidence / "join.json", "the join step", r)
    assert '"reachable": true' in joined, (
        f"`join` reported unreachable, so there was nothing to test: {joined}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )

    sent = read_marker(evidence / "send.txt", "the send step", r)
    assert sent == "SEND_EXIT=0", (
        f"`join` reported reachable but the send immediately after it failed "
        f"anyway -- the wait it does is not actually sufficient: {sent!r}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )
