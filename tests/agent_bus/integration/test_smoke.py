"""Integration / smoke test for agent-bus, in tiers.

Opt-in: these spawn a real coding agent and (tier 3) need a live Claude Code
session, so they never run in a normal `pytest tests/` sweep.

    AGENT_BUS_RUN_SPENDY_E2E_TESTS=1 uv run python -m pytest tests/integration -q -s

Tiers
-----
1. Liveness   - CLI only. Register, read an empty inbox, full file-bus round trip.
2. Each harness joins - omp, grok, codex and pi each register and get a
                message through. Needs no Claude session, so it is the tier you
                can run on demand.
3. Peer -> Claude (UDS) - a headless pi run plugs in as a native Claude peer
                and messages a live Claude session over UDS.
4. Round trip (UDS) - pi says hello, the Claude session replies, pi sees it;
                and, with the same two harnesses up, `agent-bus list` and
                Claude's own ListAgents must show exactly those two and nobody
                else.

Tiers 3 and 4 test UDS, because that is the product: a peer that appears in
Claude's native ListAgents and can be messaged like any Claude session. They
assert nothing about the bus's file layout -- the reply is read back through the
driver's own `inbox --json`, which is the public surface. To the calling agent
there is only that CLI, and to Claude there is only the socket.

Tiers 3 and 4 grade themselves from marker files the shell writes, never from
the driver's narration. A run that completed the whole round trip once failed
because pi wrote "The inbox contains a message." where the test grepped for
SEND_EXIT=0; the wake mechanism was fine and the grader was not.

Nothing is built or asserted on the Claude side. Its harness delivers the peer's
message and it answers with its native SendMessage. That absence of Claude-side
code is the feature, so a test that needs Claude to poll, read an inbox or look
up a socket is testing the wrong thing.

Why pi drives the UDS tiers
---------------------------
pi is the least capable harness here -- no MCP, no hooks, only a shell -- which
makes it the one that exercises the CLI path nothing else touches. omp drove
these before: three of four runs failed on omp's own side with MCP-shaped
errors (tools missing from its list, the send step silently skipped), and it
took minutes where pi takes seconds.

The swap is not only about speed. Driving tier 3 with pi found a real bug:
run_listen published a working socket without registering under its host pid,
so `send` could never locate it. Every other harness gets its listener from
session_start() and never touches that path. The harness with the least
machinery finds the gaps, because nothing else is papering over them.

Identity
--------
An MCP peer is registered as `pending-<pid>` by session_start() before it says
anything, then names itself -- either by the `initialize` handshake, which
tells us the harness, or by calling `register`. A shell-only peer like pi has
neither, so it runs the CLI, and `--pid $PPID` is what makes that registration
outlive the command. Tier 2 checks the *sender* on the delivered message rather
than only that something arrived, which is what proves identity was claimed.
"""

import json
import os
import re
import shutil
import subprocess
import time

import pytest
from agent_names import mint_agent_name
from claude_peer import ACK_TEXT, TICK_SECONDS
from harnesses import HARNESSES
from optin import skip_unless_opted_in

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

HAVE_PI = shutil.which("pi") is not None

pytestmark = skip_unless_opted_in


# --------------------------------------------------------------------------- helpers


def _bus_env(home, *, isolate_native=True):
    """Environment for a bus CLI call.

    isolate_native=False leaves the real sessions/socket dirs in place, which is
    required for anything that must see, or be seen by, a live Claude session.
    """
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = str(home)
    if isolate_native:
        # Point every harness registry at an empty directory too, not just the
        # sockets. `list` unions the roster with whatever discovery finds, so
        # without this a smoke assertion sees the developer's own live grok and
        # omp sessions -- and a test that sends to a name could reach a real
        # agent.
        for var, sub in (("AGENT_BUS_SESSIONS_DIR", "-sessions"),
                         ("AGENT_BUS_SOCK_DIR", "-socks"),
                         ("AGENT_BUS_GROK_DIR", "-grok"),
                         ("AGENT_BUS_OMP_DIR", "-omp")):
            env[var] = str(home) + sub
            os.makedirs(env[var], exist_ok=True)
    else:
        # The UDS tiers must see the real registries to find a live Claude peer.
        for var in ("AGENT_BUS_SESSIONS_DIR", "AGENT_BUS_SOCK_DIR",
                    "AGENT_BUS_GROK_DIR", "AGENT_BUS_OMP_DIR"):
            env.pop(var, None)
    return env


def _bus(home, *args, isolate_native=True, timeout=60):
    return subprocess.run(
        ["uv", "run", "--project", REPO, "agent-bus", *args],
        env=_bus_env(home, isolate_native=isolate_native),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _register(home, name, kind, *, pid=None, isolate_native=True):
    """Register under a pid that outlives the call.

    register() defaults to the calling process, but `uv run agent-bus` exits
    immediately and the entry is pruned as dead before the next call.
    """
    r = _bus(home, "register", "--name", name, "--kind", kind,
             "--pid", str(pid or os.getpid()), isolate_native=isolate_native)
    assert r.returncode == 0, f"register {name} failed: {r.stderr}"
    return r


def _inbox(home, name, *, isolate_native=True):
    """Messages for `name`. Raises if there is no such agent.

    This used to swallow a non-zero exit and return [], which is exactly the
    lie the product had: "inbox empty" for a target that does not exist. A
    helper that cannot tell those apart cannot test either of them.
    """
    r = _bus(home, "inbox", "--json", "--name", name, isolate_native=isolate_native)
    if r.returncode != 0:
        raise AssertionError(f"inbox --name {name} failed: {r.stderr.strip()}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        raise AssertionError(f"inbox --name {name} returned non-JSON: {r.stdout!r}") from e


def _wait_for(predicate, timeout, interval=3.0, what="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last={last!r}")


# --------------------------------------------------------------------- tier 1: liveness


def test_tier1_register_and_poll_empty_inbox(tmp_path):
    """The bus comes up in an empty directory: register, then read an empty inbox."""
    home = tmp_path / "bus"
    home.mkdir()

    name = mint_agent_name()
    _register(home, name, "other")

    listed = json.loads(_bus(home, "list", "--json").stdout)
    assert any(a["name"] == name for a in listed), listed

    assert _inbox(home, name) == []


def test_tier1_send_and_receive_on_the_file_bus(tmp_path):
    """Round trip entirely within the file bus, no agents involved."""
    home = tmp_path / "bus"
    home.mkdir()
    sender, recipient = mint_agent_name(), mint_agent_name()
    _register(home, sender, "other")
    _register(home, recipient, "other")

    r = _bus(home, "send", recipient, "-m", "ping from a", "--from-name", sender)
    assert r.returncode == 0, r.stderr

    msgs = _inbox(home, recipient)
    assert len(msgs) == 1, msgs
    assert msgs[0]["text"] == "ping from a"
    assert msgs[0]["from"]["name"] == sender
    assert msgs[0]["read"] is False


# ------------------------------------------------ tier 2: each harness joins

CLI = f"uv run --project {REPO} agent-bus"


def _join_prompt(harness, name, target):
    """Join the bus, then send. The send is what makes the test assertable.

    A headless agent is a one-shot: it registers, it exits, and its entry is
    pruned as dead before a listing can see it. That is correct behaviour --
    presence is liveness -- so asserting on `list` would be asserting that the
    agent is still running, which it deliberately is not.

    Mail is the thing that outlives its sender, so the assertion is on the
    delivered message. It is also the better assertion: the *sender* recorded
    on it proves the agent registered under the name and kind it claimed, and
    the message proves the bus carried it. One assertion, both halves.

    Two shapes, because harnesses differ in what they have. An MCP peer calls
    tools. A shell-only peer runs the CLI, and must pass `--pid $PPID`, which
    inside its own shell tool is the *agent's* pid (verified: pi's bash child
    reports its parent as `pi`) rather than the CLI process that exits at once.
    """
    if harness.joins_by == "mcp":
        return (
            "Do exactly this, nothing else.\n"
            f'1. Call the agent-bus MCP tool `register` with name="{name}" '
            f'and kind="{harness.kind}".\n'
            f'2. Call the agent-bus MCP tool `send_message` with to="{target}" '
            f'and text="hello from {name}".\n'
            "3. Print exactly JOINED=<the name field from step 1's result>.\n"
            "Do not ask questions."
        )
    return (
        "Do exactly this, nothing else.\n"
        "1. Run this bash command and print its output verbatim:\n"
        f"   {CLI} register --name {name} --kind {harness.kind} --pid $PPID\n"
        "2. Run this bash command and print its output verbatim:\n"
        f'   {CLI} send {target} -m "hello from {name}" --from-name {name}\n'
        f"3. Print exactly JOINED={name}\n"
        "Do not ask questions."
    )


@pytest.mark.parametrize(
    "harness", [pytest.param(h, id=h.name) for h in HARNESSES]
)
def test_tier2_harness_joins_the_bus_and_sends(tmp_path, harness):
    """A real agent of each kind joins the bus and gets a message through.

    Needs no Claude session, which is the point of splitting it out: this is
    the tier that can be run on demand. It covers the whole join path -- the
    harness starts our MCP server (or shells out), session_start registers it,
    and the agent claims a name -- and then proves the bus actually carried
    something.

    An MCP-only peer is registered as `pending-<pid>` before it claims,
    because the MCP child does not inherit the harness's session variables
    (grok's are hook-scoped; verified). So a message whose sender is the
    claimed name proves `register` reached us and renamed that entry.
    """
    if not harness.available:
        pytest.skip(f"{harness.binary} not on PATH")

    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()

    # A target that outlives the agent, so the mail has somewhere to land.
    holder = subprocess.Popen(["sleep", "600"])
    cleanup = harness.wire(project, home) if harness.wire else (lambda: None)
    name = mint_agent_name()
    target = mint_agent_name()
    try:
        _register(home, target, "other", pid=holder.pid)
        r = harness.run(
            harness.workdir(project), _join_prompt(harness, name, target), home=home
        )
        msgs = _inbox(home, target)
        assert msgs, (
            f"{harness.name} joined the bus but nothing arrived.\n"
            f"exit={r.returncode}\nstdout:\n{r.stdout[-3000:]}\n"
            f"stderr:\n{r.stderr[-1500:]}"
        )
        senders = {(m["from"]["name"], m["from"]["kind"]) for m in msgs}
        assert (name, harness.kind) in senders, (
            f"message arrived but not from {name!r} as {harness.kind!r}; "
            f"the agent never claimed its identity. senders={senders}"
        )
    finally:
        cleanup()
        holder.kill()
        holder.wait()


# ----------------------------------------------------- the Claude end of it

HAVE_CLAUDE = shutil.which("claude") is not None


@pytest.fixture
def e2e_peer():
    """A headless Claude session for the UDS tiers to message.

    Briefed to answer known words, so the assertion is on the reply's content
    rather than on something having arrived. Nothing is installed on either
    end; the Claude end does nothing but reply.
    """
    if not HAVE_CLAUDE:
        pytest.skip("`claude` is not on PATH")
    from claude_peer import headless_claude_peer

    with headless_claude_peer() as name:
        yield name


def _uds_prompt(home, evidence, peer, driver, *, reply: bool = False) -> str:
    """What to tell a shell-only peer so it can reach a Claude session.

    `listen` both publishes the Claude-shaped session/socket and registers the
    peer on the bus, so there is no separate register step. `--pid $PPID` is
    pi's own pid inside its shell tool, which is what makes the listener
    outlive the command that started it and be findable by the sender.

    Every step records what happened in a file under `evidence`, and the test
    reads those files rather than the driver's stdout. Asking a language model
    to relay shell output is asking it to do the one thing it will not do
    reliably: a run that completed the whole round trip still failed its
    assertion because pi wrote "The inbox contains a message." where the test
    grepped for SEND_EXIT=0. The shell records the fact; the model only has to
    run the command. What remains model-dependent -- whether it runs the step
    at all -- a missing file now reports precisely.

    Each marker is joined by `;` to the command it describes so both land in
    one shell invocation. Split across two tool calls, `$?` is somebody else's
    exit status.
    """
    steps = [
        "Do exactly this, nothing else.",
        "1. Run this bash command and print its output verbatim:",
        f"   {CLI} listen --name {driver} --pid $PPID > {home}/listen.log 2>&1 &",
        f"   sleep 6 ; echo LISTENER_UP > {evidence}/listener.txt ; echo LISTENER_UP",
        "2. Run this bash command and print its output verbatim:",
        f'   {CLI} send {peer} -m "Hello world from {driver}'
        + ('. Please reply."' if reply else '"')
        + f' ; echo "SEND_EXIT=$?" > {evidence}/send.txt ; cat {evidence}/send.txt',
    ]
    if reply:
        steps += [
            "3. Wait for the reply. Repeat at most 20 times, running this single",
            "   bash command each time and printing its output verbatim:",
            (f"   sleep 15 ; {CLI} inbox --name {driver} --json"
            f" > {evidence}/inbox.json ; cat {evidence}/inbox.json"),
            "   Stop as soon as the output contains a message.",
            "4. Print REPLY=<the text of that message> on one line, or REPLY=NONE if",
            "   the loop finished with an empty inbox.",
        ]
    else:
        steps.append("3. Print DONE.")
    steps.append("Do not ask questions.")
    return "\n".join(steps)


def _read_marker(path, step, r):
    """Read a marker file, or fail saying which step the driver never ran."""
    if not path.exists():
        raise AssertionError(
            f"the driver never ran {step}: {path.name} was not written.\n"
            f"pi stdout:\n{r.stdout[-2500:]}"
        )
    return path.read_text().strip()


def _run_pi(project, prompt, *, home, timeout=420):
    from harnesses import BY_NAME

    return BY_NAME["pi"].run(project, prompt, home=home, timeout=timeout)


# ------------------------------------------------- tier 3: peer -> Claude (UDS)


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_tier3_peer_registers_and_messages_claude_over_uds(tmp_path, e2e_peer):
    """A shell-only peer becomes a native Claude peer and messages a live session.

    Driven by pi, which is the least capable harness here -- no MCP, no hooks,
    only a shell -- and therefore the one that exercises the CLI path nothing
    else touches. omp drove this before and was replaced: three of four runs
    failed on its own side with MCP-shaped errors, and it took minutes where pi
    takes seconds.

    The extra step is the point rather than an inconvenience. Reaching a Claude
    peer needs the sender's OWN listener, because the outbound frame carries
    that socket as its reply address. omp got one free from session_start();
    pi has to ask. Doing so found a real bug -- run_listen published a working
    socket without registering under its host pid, so send could never locate
    it.

    SEND_EXIT=0 is therefore a strong assertion: it proves the listener came
    up, published a Claude-shaped session and socket, registered itself, and
    that the frame reached a live session.
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()
    # Kept out of the bus home so nothing here can be mistaken for bus state.
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    driver = mint_agent_name()
    r = _run_pi(project, _uds_prompt(home, evidence, e2e_peer, driver), home=home)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"
    sent = _read_marker(evidence / "send.txt", "the send step", r)
    assert sent == "SEND_EXIT=0", (
        f"the peer could not message the Claude session over UDS: {sent!r}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )


# ----------------------------------------------------- tier 4: round trip (UDS)


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
def test_tier4_round_trip_peer_to_claude_and_back(tmp_path, e2e_peer):
    """pi says hello over UDS; the Claude session replies; pi sees the reply.

    The Claude side does nothing and needs nothing built. Its harness delivers
    the peer's message into the conversation and it answers with its native
    SendMessage -- no plugin, no MCP, no polling. That absence is the feature,
    so this asserts nothing about the Claude side and never inspects it.

    The peer must stay alive for the round trip: its listener is what the reply
    is addressed to, and a peer that exits takes its socket with it.
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    driver = mint_agent_name()
    prompt = _uds_prompt(home, evidence, e2e_peer, driver, reply=True)
    r = _run_pi(project, prompt, home=home, timeout=900)
    assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"
    sent = _read_marker(evidence / "send.txt", "the send step", r)
    assert sent == "SEND_EXIT=0", (
        f"the peer never reached the Claude session: {sent!r}\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )

    # The reply itself, read out of the driver's own inbox rather than out of
    # its narration. For a headless peer the wording is briefed, so this also
    # proves the reply is that peer's and not an echo of the outbound message.
    body = _read_marker(evidence / "inbox.json", "the inbox poll", r)
    messages = json.loads(body) if body else []
    assert messages, (
        f"no reply arrived from {e2e_peer} within the wait.\n"
        f"pi stdout:\n{r.stdout[-2500:]}"
    )
    texts = [m.get("text", "") for m in messages]
    assert any(ACK_TEXT in t for t in texts), (
        f"a message arrived but not the briefed reply {ACK_TEXT!r}: "
        f"{texts}\npi stdout:\n{r.stdout[-2500:]}"
    )


# --------------------------- tier 4, part two: both views of the same two

# The peer's whole job here. It must call the tool on every turn, because the
# turn that matters is the one after the second harness has joined.
LIST_AGENTS_TICK = (
    "Call your ListAgents tool now, exactly once, and then say nothing else. "
    "Do not use any other tool."
)
LIST_AGENTS_BRIEF = (
    "You are a peer in an integration test for agent-bus. On every turn, call "
    "your ListAgents tool exactly once and say nothing else. Do not use any "
    "other tool and do not message anyone. Call it now."
)

# "Peer sessions (2):" -- the count Claude states in its own tool output.
_PEER_COUNT = re.compile(r"Peer sessions \((\d+)\)")


def _list_agents_results(log_dir):
    """Every ListAgents result in the peer's transcript, oldest first.

    Read out of the tool result, never out of what the peer says about it.
    Tiers 3 and 4 learned that the expensive way: a run that had completed the
    whole round trip failed because the driver narrated it in its own words.
    Here the model's only job is to call the tool; the roster it saw is the
    tool's own output.
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
            message = event.get("message") or {}
            content = message.get("content")
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
    """This test counts agents, so it needs a machine with none of its own.

    Every other test here asserts that a named thing happened, which stays
    true with bystanders present. "Two agents and no third" does not: on a
    developer's laptop the roster already holds their own sessions, and the
    assertion would fail for a reason that is not a defect.

    The container supplies the exclusivity -- its own PID namespace, HOME,
    ~/.agent-bus and /tmp/cc-socks -- so this does not create it, it reports
    whether it holds.

    The wait is not politeness. The test before this one leaves a pi peer whose
    listener exits when its host does, and it notices that on a poll, so for a
    second or two afterwards the previous test is still on the bus. Demanding
    an instantly empty bus made this skip *inside the container* -- a test that
    passed by not running, which is the failure this suite exists to catch.
    """
    deadline = time.time() + drain_timeout
    rows = []
    while time.time() < deadline:
        r = _bus(home, "list", "--json", isolate_native=False)
        assert r.returncode == 0, f"list failed: {r.stderr}"
        rows = json.loads(r.stdout or "[]")
        if not rows:
            return
        time.sleep(1.0)
    pytest.skip(
        f"this test counts every agent on the machine, and {len(rows)} were "
        f"still here after {drain_timeout:.0f}s: "
        f"{[a.get('name') for a in rows]}. Run it where nothing else is: "
        "`docker compose run --rm e2e`."
    )


def _two_harness_prompt(home, evidence, driver) -> str:
    """Join, stay joined long enough for the Claude peer to look, then report.

    The sleep is load-bearing. `--pid $PPID` ties the listener's life to pi's
    shell, so everything the Claude side is meant to see has to happen while
    pi is still running -- and the peer only looks once per tick.
    """
    return "\n".join([
        "Do exactly this, nothing else.",
        "1. Run this bash command and print its output verbatim:",
        f"   {CLI} listen --name {driver} --pid $PPID > {home}/listen.log 2>&1 &",
        f"   sleep 6 ; echo LISTENER_UP > {evidence}/listener.txt ; echo LISTENER_UP",
        "2. Run this bash command and print its output verbatim:",
        (f"   sleep {int(TICK_SECONDS) + 20} ; {CLI} list --json"
         f" > {evidence}/list.json ; echo LIST_TAKEN"),
        "3. Print DONE.",
        "Do not ask questions.",
    ])


@pytest.mark.skipif(not HAVE_PI, reason="pi not on PATH")
@pytest.mark.skipif(not HAVE_CLAUDE, reason="claude not on PATH")
def test_tier4_two_harnesses_see_each_other_and_nobody_else(tmp_path):
    """Two harnesses, two views of them, and the two views must agree.

    Every other tier asks whether a thing arrived. This one asks who is *there*,
    which is the question a listing exists to answer and the one that was being
    answered wrongly: a peer that published a listener was counted twice, once
    as itself and once as its own socket.

    Both views are checked because either alone can be right while the product
    is wrong. `agent-bus list` is ours; Claude's ListAgents is the harness's own,
    reading the session file we publish. A disagreement between them means one
    of the two is lying about the team, and a sender cannot tell which.

    The second harness is pi deliberately -- no MCP, no hooks, just a shell
    running `listen`. It publishes exactly the Claude-shaped session that made
    the duplicate, so this is the path under test rather than a convenient one.
    """
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "bus"
    home.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    peer_log = tmp_path / "peer"
    peer_log.mkdir()

    driver = mint_agent_name()
    _require_an_exclusive_bus(home)

    from claude_peer import headless_claude_peer

    with headless_claude_peer(
        brief=LIST_AGENTS_BRIEF, tick=LIST_AGENTS_TICK, log_dir=str(peer_log)
    ) as claude_name:
        r = _run_pi(project, _two_harness_prompt(home, evidence, driver), home=home)
        assert r.returncode == 0, f"pi exited {r.returncode}: {r.stderr[-1500:]}"
        _read_marker(evidence / "listener.txt", "the listen step", r)

        # --- our view -------------------------------------------------------
        raw = _read_marker(evidence / "list.json", "the list step", r)
        rows = json.loads(raw)
        names = sorted(a["name"] for a in rows)
        assert len(rows) == 2, (
            f"`agent-bus list` shows {len(rows)} agents, expected exactly two "
            f"-- the Claude session and the pi peer: {names}"
        )
        assert claude_name in names, f"the Claude session is missing: {names}"
        assert driver in names, f"the pi peer is missing: {names}"
        kinds = sorted(a["kind"] for a in rows)
        # `other` is a positive answer, not a missing one: this peer works,
        # and we have no discovery adapter that can name what it is. pi is
        # the standing example, and it stays `other` however well it works.
        assert kinds == ["claude", "other"], kinds

    # --- the harness's own view --------------------------------------------
    # Read after the peer has stopped, so the transcript is complete. Claude
    # omits itself from ListAgents, so one peer session is the whole team.
    results = _list_agents_results(str(peer_log))
    assert results, (
        "the Claude peer never produced a ListAgents result; it cannot have "
        f"looked. transcript: {peer_log}"
    )
    seen = [(int(m.group(1)), t) for t in results if (m := _PEER_COUNT.search(t))]
    assert seen, f"no ListAgents result stated a peer count: {results[-1][:400]}"
    counts = [n for n, _ in seen]
    assert max(counts) <= 1, (
        f"Claude saw more than one peer at some point: {counts}. With two "
        "harnesses running and Claude omitting itself, anything above one is a "
        "row that is not an agent."
    )
    saw_the_peer = [t for n, t in seen if n == 1]
    assert saw_the_peer, (
        "Claude never saw exactly one peer, so it never saw the pi peer join: "
        f"{[t[:120] for t in results]}"
    )
    assert driver in saw_the_peer[-1], (
        f"the one peer Claude saw was not {driver}:\n{saw_the_peer[-1][:400]}"
    )
