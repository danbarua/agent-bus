"""A real MCP-driven agent sets its own status and `self` reflects it back,
agreeing with what `list_agents` shows for the same entry.

#171's Tier 2: "self -- worth having now that #125 changed what it answers
for an unregistered session" and "status / MCP set_status -- presence is read
by every listing." Both named cheap, one line on an existing prompt, so this
is one small test covering both tools rather than two.

#125's own PR is explicit that every one of its new tests stubs discovery
rather than driving it live ("pytest's ancestor chain contains a live Claude
session on a developer's machine and nothing on CI" is the trap it names) --
so `self` has unit coverage of the branch logic and zero coverage of a real
harness actually calling it. This test does not attempt #125's harder case
(an unregistered-but-discovered session): that needs a live Claude session
acting as its own driver, a materially bigger test than "cheap, one line."
What it closes is the plainer gap underneath: nothing had ever called
`self`/`set_status` as real MCP tools at all, registered or not.

Driven by `codex`, the same cheap, no-wiring MCP harness as
`test_join_reaches_a_claude_session.py` and
`test_mcp_inbox_and_ack_close_the_loop.py`, for the same reason -- the test
is about the tools, not about codex.

First draft checked the roster from outside, after `codex exec` returned, via
`busctl.bus(..., "list", "--json")` -- and got an empty roster back, every
time, `set_status` notwithstanding. Not a bug: this is `test_a_harness_joins_
the_bus.py`'s own lesson ("presence is liveness... asserting it appears in
`list` would be asserting it is still running") applied to a field this
suite had not hit before. Mail outlives its sender; a roster entry's status
does not outlive the process that holds it. A one-shot MCP harness's entry
is pruned the moment its process exits, so nothing outside that process can
read its status back afterward -- there is no post-exit check to write here.

So both checks happen inside the one live run instead: `self`'s reported
status (`SELF=name,status`) and a separate `list_agents` call finding the
same entry (`LISTED=status`) -- two different MCP tools reading the same
roster entry, agreeing while it is still alive, which is the only window in
which either can be checked for a harness this short-lived. Both relayed as
a single strict token each, no shell to write a marker file to, same as
`mcp_inbox_and_ack.md`'s precedent.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is alongside this file: test_self_reflects_a_status_it_just_set.md
"""

import re

import pytest
from agent_names import mint_agent_name
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

CODEX = BY_NAME["codex"]
STATUS = "reviewing e2e coverage"


def _line(pattern, stdout):
    m = re.search(pattern, stdout, re.MULTILINE)
    return m.group(1) if m else None


@pytest.mark.skipif(not CODEX.available, reason="codex not on PATH")
def test_self_and_list_agents_agree_after_set_status(project, bus_home):
    driver = mint_agent_name()
    prompt = render("mcp_self_and_status", driver=driver, kind=CODEX.kind, status=STATUS)
    r = CODEX.run(CODEX.workdir(project), prompt, home=bus_home)
    assert r.returncode == 0, f"codex exited {r.returncode}: {r.stderr[-1500:]}"

    m = re.search(r"^SELF=([^,]*),(.*)$", r.stdout, re.MULTILINE)
    assert m, f"codex never printed SELF=...\ncodex stdout:\n{r.stdout[-2500:]}"
    self_name, self_status = m.group(1), m.group(2)
    assert self_name == driver, (
        f"self reported the wrong name: {self_name!r}\n"
        f"codex stdout:\n{r.stdout[-2500:]}"
    )
    assert self_status == STATUS, (
        f"set_status reported success but self still shows {self_status!r}\n"
        f"codex stdout:\n{r.stdout[-2500:]}"
    )

    listed = _line(r"^LISTED=(.*)$", r.stdout)
    assert listed == STATUS, (
        f"self and list_agents disagree: self said {self_status!r}, "
        f"list_agents said {listed!r}\ncodex stdout:\n{r.stdout[-2500:]}"
    )
