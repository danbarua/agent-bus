"""Two agents are on the bus, and both views of it show exactly those two.

Every other test here asks whether a named thing happened, which stays true with
bystanders around. This one asks who is *there* -- the question a listing exists
to answer, and the one that was being answered wrongly: a peer that published a
listener was counted twice, once as itself and once as its own socket.

Both views, because either can be right while the product is wrong.
`agent-bus list` is our answer; Claude's `ListAgents` is the harness's own, read
from the session file we publish. When they disagree, one of them is lying about
who is on the team and a sender cannot tell which.

Counting is why this needs a machine with no agents of its own. The container
gives that -- its own PID namespace, HOME, ~/.agent-bus and /tmp/cc-socks -- so
this reports whether it holds rather than creating it, and skips outside one
naming whoever it found.

A real sequence diagram from this test is in docs/e2e-scenarios.md, including
the part its own structured log cannot show: what Claude's native ListAgents
actually returned, only visible in its own transcript.
"""

import json
import os
import re
import time

import pytest
from agent_names import mint_agent_name
from busctl import CLI, bus, read_marker
from claude_peer import TICK_SECONDS, headless_claude_peer
from harnesses import BY_NAME
from optin import skip_unless_opted_in
from prompts import render

pytestmark = [pytest.mark.spendy, skip_unless_opted_in]

# "Peer sessions (2):" -- the count Claude states in its own tool output.
PEER_COUNT = re.compile(r"Peer sessions \((\d+)\)")


def _list_agents_results(log_dir):
    """Every ListAgents result in the peer's transcript, oldest first.

    Read out of the tool result, never out of what the peer says about it. The
    model's only job is to call the tool; the roster it saw is the tool's output.
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

    The wait is not politeness. A test before this one leaves a peer whose
    listener exits when its host does and notices that on a poll, so for a
    second or two the previous test is still on the bus. Demanding an instantly
    empty one made this skip *inside the container* -- a test that passed by not
    running.
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
        f"this test counts every agent on the machine, and {len(rows)} were still "
        f"here after {drain_timeout:.0f}s: {[a.get('name') for a in rows]}. "
        "Run it where nothing else is: `docker compose run --rm e2e`."
    )


@pytest.mark.skipif(not BY_NAME["pi"].available, reason="pi not on PATH")
def test_a_claude_session_and_a_peer_are_two_agents_in_both_views(
    project, bus_home, evidence, tmp_path
):
    peer_log = tmp_path / "peer"
    peer_log.mkdir()
    driver = mint_agent_name()
    _require_an_exclusive_bus(bus_home)

    # The second agent is pi deliberately: no MCP, no hooks, just a shell
    # running `listen`. That publishes exactly the Claude-shaped session that
    # produced the duplicate, so this drives the path under test.
    with headless_claude_peer(
        brief=render("claude_peer_list_agents"),
        tick=render("claude_peer_list_agents_tick"),
        log_dir=str(peer_log),
    ) as claude_name:
        prompt = render("uds_listen_and_stay", cli=CLI, driver=driver,
                        home=bus_home, evidence=evidence,
                        stay_seconds=int(TICK_SECONDS) + 20)
        r = BY_NAME["pi"].run(project, prompt, home=bus_home)
        assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"
        read_marker(evidence / "listener.txt", "the listen step", r)

        rows = json.loads(read_marker(evidence / "list.json", "the list step", r))
        names = sorted(a["name"] for a in rows)
        assert len(rows) == 2, (
            f"`agent-bus list` shows {len(rows)} agents, expected exactly two -- "
            f"the Claude session and the pi peer: {names}"
        )
        assert claude_name in names, f"the Claude session is missing: {names}"
        assert driver in names, f"the peer is missing: {names}"
        # `other` is a positive answer, not a missing one: the peer works, and
        # no discovery adapter can name what it is.
        assert sorted(a["kind"] for a in rows) == ["claude", "other"]

    # Read after the peer has stopped, so its transcript is complete. Claude
    # omits itself from ListAgents, so one peer session is the whole team.
    results = _list_agents_results(str(peer_log))
    assert results, (
        f"the Claude peer never produced a ListAgents result; it cannot have "
        f"looked. transcript: {peer_log}"
    )
    seen = [(int(m.group(1)), t) for t in results if (m := PEER_COUNT.search(t))]
    assert seen, f"no ListAgents result stated a peer count: {results[-1][:400]}"
    assert max(n for n, _ in seen) <= 1, (
        f"Claude saw more than one peer: {[n for n, _ in seen]}. With two agents "
        "running and Claude omitting itself, anything above one is a row that is "
        "not an agent."
    )
    saw_the_peer = [t for n, t in seen if n == 1]
    assert saw_the_peer, (
        f"Claude never saw exactly one peer, so it never saw {driver} join: "
        f"{[t[:120] for t in results]}"
    )
    assert driver in saw_the_peer[-1], (
        f"the one peer Claude saw was not {driver}:\n{saw_the_peer[-1][:400]}"
    )
