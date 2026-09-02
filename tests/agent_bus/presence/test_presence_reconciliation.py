"""One agent, one row.

Reproduces the duplicate that was live on the author's machine:

    claude-a4775baa   claude  58291  idle   8054898a-70b8-4f16-...
    exo-ledger        claude  58291  busy   claude:a4775baa-d875-...

One process. Registered under a bus uuid, discovered under the harness's own
session address, merged on id alone -- so the two never reconciled and the one
view whose job is to make harnesses look alike double-counted.
"""
import contextlib
import json
import os
import subprocess
import time

import pytest
from roster import found

from agent_bus import store
from agent_bus.address import SESSION, mint
from agent_bus.protocol import AgentTarget


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path / "bus"))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    store.ensure_dirs(str(tmp_path / "bus"))
    return str(tmp_path / "bus"), sessions


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def _publish_session(sessions, pid, sid, name, agent_bus=False):
    data = {"pid": pid, "sessionId": sid, "name": name, "cwd": "/tmp",
            "status": "busy"}
    if agent_bus:
        data["agentBus"] = True
    (sessions / f"{pid}.json").write_text(json.dumps(data))


def test_a_registered_and_discovered_agent_is_one_row(bus, holder):
    """The exact shape found on disk: a roster entry with no procStart, and a
    session file for the same pid under a different address."""
    home, sessions = bus
    sid = "a4775baa-d875-456c-ab27-1bb45511426d"
    store.register("claude-a4775baa", "claude", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, sid, "exo-ledger")

    rows = [a for a in store.list_agents(home=home) if a.pid == holder.pid]
    assert len(rows) == 1, [(r.name, str(r.id)) for r in rows]


def test_the_merged_row_keeps_the_claimed_name_and_takes_live_status(bus, holder):
    """Roster is authoritative for identity, discovery for what changes."""
    home, sessions = bus
    store.register("claimed-name", "claude", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, "sid-1", "harness-name")

    row = next(a for a in store.list_agents(home=home) if a.pid == holder.pid)
    assert row.name == "claimed-name"
    assert row.status == "busy"


def test_an_alias_makes_the_link_explicit(bus, holder):
    home, sessions = bus
    sid = "sid-explicit"
    alias = str(mint("claude", SESSION, sid))
    store.register("mine", "claude", pid=holder.pid, home=home, aliases=[alias])
    _publish_session(sessions, holder.pid, sid, "other-name")

    rows = [a for a in store.list_agents(home=home) if a.pid == holder.pid]
    assert len(rows) == 1
    assert store.find_entry(AgentTarget(alias), home) is not None


def test_session_start_records_the_harness_address(tmp_path, monkeypatch):
    """describe() always resolved the session id and then discarded it."""
    from agent_bus.lifecycle import SessionDescriptor, session_start

    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        desc = SessionDescriptor(
            kind="grok", session_id="sid-42", pid=holder.pid, cwd="/tmp", name="g"
        )
        entry = session_start(descriptor=desc, home=home)
        assert "grok:session:sid-42" in entry.aliases
        assert entry.native.get("sessionId") == "sid-42"
        assert store.find_entry(AgentTarget("grok:session:sid-42"), home) is not None
    finally:
        holder.kill()
        holder.wait()


def test_two_different_agents_are_still_two_rows(bus, holder):
    """The merge must not collapse genuinely distinct agents."""
    home, _sessions = bus
    other = subprocess.Popen(["sleep", "60"])
    try:
        store.register("one", "grok", pid=holder.pid, home=home)
        store.register("two", "grok", pid=other.pid, home=home)
        names = {a.name for a in store.list_agents(home=home)}
        assert {"one", "two"} <= names
    finally:
        other.kill()
        other.wait()


def test_a_different_kind_on_the_same_pid_is_not_merged(bus, holder):
    """(kind, pid) not pid alone -- a listener and its host share a pid."""
    home, sessions = bus
    store.register("grok-one", "grok", pid=holder.pid, home=home)
    _publish_session(sessions, holder.pid, "sid-x", "claude-one")
    kinds = {a.kind for a in store.list_agents(home=home) if a.pid == holder.pid}
    assert kinds == {"grok", "claude"}


def test_aliases_survive_a_disk_round_trip(bus, holder):
    home, _ = bus
    store.register("x", "grok", pid=holder.pid, home=home, aliases=["grok:session:s1"])
    assert found(AgentTarget("x"), home).aliases == ["grok:session:s1"]


def test_a_listener_registers_in_the_bus_it_was_given(tmp_path, monkeypatch):
    """start_uds_listen took a `home` and did not pass it to the child.

    The listener is a separate process that registers itself, so a caller that
    set the home by argument rather than by env got a listener registering in
    the *default* bus. Under test that wrote real entries into the developer's
    own ~/.agent-bus on every run.
    """
    from agent_bus import listener

    monkeypatch.delenv("AGENT_BUS_HOME", raising=False)
    captured = {}

    class _Proc:
        pid = 4242

    def _fake_popen(argv, **kw):
        captured["env"] = kw.get("env") or {}
        return _Proc()

    monkeypatch.setattr(listener.subprocess, "Popen", _fake_popen)
    listener.start_uds_listen("some-peer", 999, home=str(tmp_path))
    assert captured["env"].get("AGENT_BUS_HOME") == str(tmp_path)


# ------------------------ a peer and the session file its listener writes


@pytest.fixture
def listener_holder():
    """A second live pid: the listener publishes under its own, not its host's."""
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def _publish_session_for(sessions, entry, listener_pid, home):
    """The two things a merge takes: an entry carrying an address, and a
    session file published at it.

    Built directly, because that is what these tests are about. Whether a
    listener produces this pair is a different question, and
    test_a_real_listener_records_the_address_it_publishes is where it is asked.
    """
    store.register(entry.name, entry.kind, pid=entry.pid, home=home,
                   aliases=[str(mint("agentbus", SESSION, entry.id))])
    _publish_session(sessions, listener_pid, entry.id, entry.name, agent_bus=True)


def test_a_peer_with_a_listener_is_one_row(bus, holder, listener_holder):
    """A peer must not appear twice for having become reachable.

    The listener is its own process and writes its session file under its
    own pid, so a peer
    registered under a *host* pid -- every MCP harness -- matches on neither
    id nor (kind, pid). Nothing reconciled the two, and the peer was listed as
    itself and again as its own socket.

    An alias is what says two addresses are one agent. This is the same fix
    "one agent, one row" made for a harness's session address, applied to the
    one address a peer writes for itself.
    """
    home, sessions = bus
    entry = store.register("omp-peer", "omp", pid=holder.pid, home=home)
    _publish_session_for(sessions, entry, listener_holder.pid, home)

    rows = [a for a in store.list_agents(home=home) if a.name == "omp-peer"]
    assert len(rows) == 1, [(r.name, r.kind, r.pid, str(r.id)) for r in rows]


def test_the_merged_row_is_the_registered_one(bus, holder, listener_holder):
    """Merging onto the wrong row would keep the name and lose the agent.

    The roster entry carries the kind the agent claimed and the pid of the
    process doing the work. The listener's row carries neither -- it is a
    socket -- so a merge that kept it would leave something that looks
    addressable and is a courier.
    """
    home, sessions = bus
    entry = store.register("omp-peer", "omp", pid=holder.pid, home=home)
    _publish_session_for(sessions, entry, listener_holder.pid, home)

    row = next(a for a in store.list_agents(home=home) if a.name == "omp-peer")
    assert row.id == entry.id
    assert row.kind == "omp"
    assert row.pid == holder.pid


def test_the_published_address_survives_the_peer_being_claimed(bus, holder, listener_holder):
    """A listener starts before anyone has said who they are, and is claimed later.

    Nothing knows a peer's identity when its socket binds: no harness uses a
    session-start hook, the MCP ones attach at server start -- where Codex
    passes its child no session id at all -- and pi runs `listen` from its
    shell. So the entry is pending and the agent names itself afterwards.

    The alias is minted from the entry id, which register() keeps across a
    rename, so the claim moves the name and the address still resolves. Minting
    it from anything that changes would strand the published session.
    """
    home, sessions = bus
    pending = store.register(f"other-{holder.pid}", "other", pid=holder.pid, home=home)
    _publish_session_for(sessions, pending, listener_holder.pid, home)

    from agent_bus.commands import agents as agents_cmd
    agents_cmd.register("omp-peer", "omp", pid=holder.pid, home=home)

    rows = [a for a in store.list_agents(home=home)
            if a.pid in (holder.pid, listener_holder.pid)]
    assert len(rows) == 1, [(r.name, r.kind, str(r.id)) for r in rows]
    assert (rows[0].name, rows[0].kind) == ("omp-peer", "omp")


def test_a_session_file_with_no_alias_is_its_own_row(bus, listener_holder):
    """A listener from before this fix keeps running, and stays addressable.

    Its session file names an address no roster entry claims, so it lists as
    its own row -- which is right. It is also how a listener belonging to a
    *different* AGENT_BUS_HOME appears at all: a genuine peer, not a duplicate.
    """
    home, sessions = bus
    (sessions / f"{listener_holder.pid}.json").write_text(json.dumps({
        "pid": listener_holder.pid, "sessionId": "unclaimed-sid",
        "name": "legacy-peer", "agentBus": True, "cwd": "/tmp", "status": "idle",
    }))

    rows = [a for a in store.list_agents(home=home) if a.name == "legacy-peer"]
    assert len(rows) == 1
    assert str(rows[0].id) == "agentbus:unclaimed-sid"


def test_a_real_listener_records_the_address_it_publishes(tmp_path, holder, monkeypatch):
    """The tests above prove the merge; this proves the listener feeds it.

    They give list_agents an alias and check it reconciles, which is the
    machinery "one agent, one row" already built. What was missing was anyone
    recording the alias for the address a peer writes for *itself*, so this
    starts a real listener and reads the roster back.

    Short socket dir on purpose: AF_UNIX caps the path near 104 bytes and
    pytest's tmp_path is most of that already -- see test_conventions.py.
    """
    import secrets
    import shutil

    from agent_bus import address, listener

    base = f"/tmp/ab-{secrets.token_hex(4)}"
    socks = f"{base}/s"
    os.makedirs(socks, exist_ok=True)
    home = str(tmp_path / "bus")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", socks)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    try:
        entry = store.register("omp-peer", "omp", pid=holder.pid, home=home)
        listener_pid = listener.start_uds_listen(entry.name, holder.pid, home=home)
        assert listener_pid, "no listener was started"
        deadline = time.time() + 15
        expected = str(address.mint("agentbus", address.SESSION, entry.id))
        while time.time() < deadline:
            back = store.find_entry(AgentTarget("omp-peer"), home)
            if back and expected in back.aliases:
                break
            time.sleep(0.2)
        assert back and expected in back.aliases, (
            f"the listener published a session but recorded no address for it; "
            f"aliases={back.aliases if back else None}"
        )
        rows = [a for a in store.list_agents(home=home) if a.name == "omp-peer"]
        assert len(rows) == 1, [(r.kind, r.pid, str(r.id)) for r in rows]
    finally:
        with contextlib.suppress(Exception):
            listener.stop_uds_listen(holder.pid, home=home)
        shutil.rmtree(base, ignore_errors=True)


def test_a_listener_waits_for_the_host_that_spawned_it(tmp_path, holder, monkeypatch):
    """Losing that race splits one peer into two, under two names.

    The listener is detached, so it can read the roster before its parent's
    registration lands. It used to give up immediately and claim the requested
    name under its *own* pid -- which then renamed the parent's registration to
    `<name>-2`, leaving the caller holding an id that no longer matched the name
    it asked for.

    A bridge saw that as itself appearing in the roster it publishes to the
    desktop peer: the snapshot excludes its own id, and its own id was now on
    the `-2` row.
    """
    import secrets
    import shutil

    from agent_bus import listener

    base = f"/tmp/ab-{secrets.token_hex(4)}"
    socks = f"{base}/s"
    os.makedirs(socks, exist_ok=True)
    home = str(tmp_path / "bus")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", socks)
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    try:
        # The listener starts first: the window the parent normally closes.
        assert listener.start_uds_listen("desktop-claude", holder.pid, home=home)
        time.sleep(1.0)
        store.register("desktop-claude", "desktop", pid=holder.pid, home=home)

        deadline = time.time() + 15
        rows = []
        while time.time() < deadline:
            rows = [a for a in store.list_agents(home=home)
                    if a.name.startswith("desktop-claude")]
            if rows and any(a.kind == "desktop" for a in rows):
                break
            time.sleep(0.2)

        assert [a.name for a in rows] == ["desktop-claude"], (
            f"the listener claimed a second identity: "
            f"{[(a.name, a.kind, a.pid) for a in rows]}"
        )
        assert rows[0].pid == holder.pid, "the surviving row is not the host's"
    finally:
        with contextlib.suppress(Exception):
            listener.stop_uds_listen(holder.pid, home=home)
        shutil.rmtree(base, ignore_errors=True)
