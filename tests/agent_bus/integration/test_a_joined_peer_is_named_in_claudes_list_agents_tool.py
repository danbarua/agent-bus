"""A joined peer's real name reaches Claude's native `ListAgents` tool call --
the surface `SendMessage`/cross-session addressing actually depends on.

**Why this exists instead of a test for #200 directly.** #200 reported every
agent-bus session showing as "(unnamed session)" in Claude's own
`/list-agents`. Live-verified before writing this, not assumed: the bug is
real, but it lives specifically in `/list-agents` and @-mention -- Claude
Code's human-typed listing surfaces -- and does not reach the `ListAgents`
**tool** a model calls. Checked directly: against the same live bridge
session, `/list-agents` (run by a human) showed `(unnamed session)`; the
`ListAgents` tool, called moments later, returned the real name.

agent-bus has no part in that divergence. It publishes one Claude-shaped
session file and binds one UDS socket -- the entirety of its involvement --
and has no visibility into, or influence over, how Claude Code's two listing
features each choose to render what was published. Whatever produces the
difference is entirely inside Claude Code, between those two features.

That also means #200's actual bug has no automatable path: a pytest/docker
e2e test drives a model calling tools, and there is nothing for it to call
that exercises a human-typed slash command or an @-mention. No container
image or Claude Code version changes that -- it is not a reproduction gap,
it is a gap in what is automatable at all for this specific bug. #200 stays
open, real, and verifiable only by a human running `/list-agents` directly.

What *is* real, automatable, and worth a regression guard: the tool path is
unaffected. This test proves that, so a future change to how agent-bus
publishes a session file (in service of a #200 fix or anything else) cannot
silently break the one listing surface that routing actually depends on.

The ask that shaped this test's shape was specific on purpose: brief Claude
with nothing but "call your ListAgents tool" -- no mention of agent-bus, no
instruction about what to do with what it sees. `claude_peer_list_agents.md`
already says exactly that; reused verbatim rather than rewritten, so this
tests a stock ListAgents call, not a coached one.

Run inside the container. `agent-bus join` publishes a Claude-shaped session
file, so Claude's ListAgents result includes everything else on the machine
that looks like one too -- the container's own PID namespace, HOME and
`~/.claude/sessions` are what make the one peer this test joins the only
session there is to see, so a result naming it is unambiguous.

A real sequence diagram from this test, built from a real capture and not
from this docstring, is alongside this file.
"""

import json
import os
import time

import pytest
from agent_names import mint_agent_name
from busctl import CLI, bus
from claude_peer import TICK_SECONDS, headless_claude_peer
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

UNNAMED = "(unnamed session)"


def _list_agents_results(log_dir):
    """Every ListAgents result in the peer's transcript, oldest first.

    Mirrors `test_both_views_of_the_roster_agree.py`'s helper of the same
    name and purpose: read out of the tool result, never out of what the
    peer says about it. The model's only job is to call the tool; the
    listing it saw is the tool's own output, not a paraphrase of it.
    """
    out = []
    path = os.path.join(log_dir, "stdout.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            content = (event.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block.get("content")
                    text = body if isinstance(body, str) else json.dumps(body)
                    if "Peer sessions" in text:
                        out.append(text)
    return out


def _require_an_exclusive_bus(home, drain_timeout=20.0):
    """Skip unless this machine has no agents of its own.

    Same guard and the same reason as `test_both_views_of_the_roster_agree.py`:
    this test's claim is about what a real ListAgents call renders for the
    one peer it joins, and a stray session on the machine is a second, real
    row that makes the transcript harder to read as evidence of just this.
    """
    deadline = time.time() + drain_timeout
    rows = []
    while time.time() < deadline:
        r = bus(home, "list", "--json", isolate_native=False)
        assert r.returncode == 0, f"list failed: {r.stderr}"
        rows = json.loads(r.stdout or "[]")
        if not rows:
            return
        time.sleep(1.0)
    pytest.skip(
        f"this test reads a real ListAgents call meant to show one peer, and "
        f"{len(rows)} others were still here after {drain_timeout:.0f}s: "
        f"{[a.get('name') for a in rows]}. "
        "Run it where nothing else is: `docker compose run --rm e2e`."
    )


@pytest.mark.skipif(not BY_NAME["pi"].available, reason="pi not on PATH")
def test_a_joined_peers_real_name_reaches_the_list_agents_tool(
    project, bus_home, evidence, tmp_path
):
    peer_log = tmp_path / "peer"
    peer_log.mkdir()
    driver = mint_agent_name()
    _require_an_exclusive_bus(bus_home)

    with headless_claude_peer(
        brief=render("claude_peer_list_agents"),
        tick=render("claude_peer_list_agents_tick"),
        log_dir=str(peer_log),
    ):
        prompt = render("join_and_stay", cli=CLI, driver=driver,
                        evidence=evidence, stay_seconds=int(TICK_SECONDS) + 20)
        r = BY_NAME["pi"].run(project, prompt, home=bus_home)
        assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"

        joined = (evidence / "join.json").read_text() if (evidence / "join.json").exists() else ""
        assert '"reachable": true' in joined, (
            f"`join` never reported reachable, so there was nothing for Claude "
            f"to see: {joined}\npi stdout:\n{r.stdout[-2500:]}"
        )

    results = _list_agents_results(str(peer_log))
    assert results, (
        f"the Claude peer never produced a ListAgents result; it cannot have "
        f"looked. transcript: {peer_log}"
    )

    last = results[-1]
    assert driver in last, (
        f"the ListAgents tool never named the joined peer by its real name "
        f"({driver!r}) -- this is the surface #200 found NOT to be affected; "
        f"if this now fails, something changed that regressed it.\n"
        f"result:\n{last[:800]}"
    )
    assert UNNAMED not in last, (
        f"the ListAgents tool result contains {UNNAMED!r} -- #200's bug, "
        f"previously confirmed to be specific to `/list-agents` and "
        f"@-mention, appears to now reach the tool path too. Re-verify "
        f"directly (a human running `/list-agents`) before concluding "
        f"anything from this alone.\nresult:\n{last[:800]}"
    )
