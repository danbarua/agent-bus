"""The CLI and the MCP server must not be able to disagree.

Every test here failed, or could not have been written, before the command
layer existed: the two surfaces each carried their own copy of these
operations and had already drifted apart in four places.
"""
import json
import os
import subprocess

import pytest

from agent_bus.cli import main
from agent_bus.commands import agents as agents_cmd
from agent_bus.mcp_server import handle_rpc
from agent_bus.protocol import resolve_kind_filter
from agent_bus.store import load_roster
from agent_bus.store import register as store_register


def _tool(name, args, _id=1):
    resp = handle_rpc({
        "jsonrpc": "2.0",
        "id": _id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })
    assert "error" not in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def holder():
    """A live process to hang a roster entry on, so pruning leaves it alone."""
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


# --- the filter that meant two different things ---------------------------

@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("   ", None),
    ("all", None), ("ALL", None), (" All ", None),
    ("claude", "claude"), ("Claude", "claude"), (" GROK ", "grok"),
    ("never-heard-of-it", "never-heard-of-it"),
])
def test_resolve_kind_filter(value, expected):
    assert resolve_kind_filter(value) == expected


@pytest.mark.parametrize("kind", ["all", "ALL", " All "])
def test_both_surfaces_agree_that_all_means_all(bus, holder, capsys, kind):
    """`kind="ALL"` returned everything from the CLI and nothing from MCP.

    The MCP tool's own description invites the word "all", so a caller that
    capitalised it asked for a harness literally named "all" and was told,
    truthfully and uselessly, that there were none.
    """
    store_register("cased", "claude", pid=holder.pid, home=bus)

    via_mcp = _tool("list_agents", {"kind": kind})
    assert any(a["name"] == "cased" for a in via_mcp), (kind, via_mcp)

    assert main(["list", "--json", "--kind", kind]) == 0
    via_cli = json.loads(capsys.readouterr().out)
    assert [a["id"] for a in via_cli] == [a["id"] for a in via_mcp]


def test_unknown_kind_filters_to_nothing_on_both(bus, holder, capsys):
    store_register("cased", "claude", pid=holder.pid, home=bus)
    assert _tool("list_agents", {"kind": "no-such-harness"}) == []
    assert main(["list", "--json", "--kind", "no-such-harness"]) == 0
    assert json.loads(capsys.readouterr().out) == []


# --- the serializer that existed three times ------------------------------

def test_inbox_is_serialized_identically_by_both_surfaces(bus, holder, capsys):
    store_register("sender", "other", pid=os.getpid(), home=bus)
    store_register("target", "other", pid=holder.pid, home=bus)
    main(["send", "target", "-m", "body text", "--summary", "sum", "--from-name", "sender"])
    capsys.readouterr()

    via_mcp = _tool("get_inbox", {"name": "target"})
    assert main(["inbox", "--name", "target", "--json"]) == 0
    via_cli = json.loads(capsys.readouterr().out)

    assert via_cli == via_mcp
    assert via_cli[0]["text"] == "body text"
    # The canonical envelope: "from" as a nested ref, not a flattened name.
    assert set(via_cli[0]) == {
        "id", "ts", "from", "to", "summary", "text", "replyTo", "read"
    }
    assert set(via_cli[0]["from"]) == {"id", "name", "kind"}


# --- the entry described two ways ----------------------------------------

def test_self_is_described_identically_by_both_surfaces(bus, capsys):
    store_register("me", "other", pid=os.getpid(), home=bus)

    via_mcp = _tool("self", {})
    assert main(["self", "--json"]) == 0
    via_cli = json.loads(capsys.readouterr().out)

    assert via_cli == via_mcp
    # The parity is the point: cmd_self used to hand-build its own keys while
    # list --json used the serializer, so the two surfaces disagreed.
    #
    # This used to also assert "procStart" in via_cli. procStart is the internal
    # pid-reuse guard, and it was exposed so the guard would not *look* inert to
    # someone reading `self --json` -- a diagnostic reason to publish an
    # implementation detail. It is not a caller's field. The guard is tested
    # where it lives, in test_presence_vs_mailbox.
    assert "procStart" not in via_cli, "internal guard field, not a caller's"
    assert "inbox" not in via_cli, "a path to a file on disk"
    assert via_cli["registered"] is True


def test_list_and_self_describe_an_entry_the_same_way(bus, capsys):
    store_register("me", "other", pid=os.getpid(), home=bus)
    main(["self", "--json"])
    me = json.loads(capsys.readouterr().out)
    main(["list", "--json"])
    listed = next(a for a in json.loads(capsys.readouterr().out) if a["name"] == "me")
    assert listed == {k: v for k, v in me.items() if k != "registered"}


# --- the pid a registration is for ---------------------------------------

def test_explicit_pid_wins(bus, holder):
    assert agents_cmd._host_pid(holder.pid, bus) == holder.pid


def test_host_pid_adopts_this_process_registration(bus, monkeypatch):
    """Without adoption a second register() makes a duplicate, not a rename."""
    class _Entry:
        pid = 4242
    monkeypatch.setattr(agents_cmd.store, "get_self", lambda home=None: _Entry())
    assert agents_cmd._host_pid(None, bus) == 4242


def _no_session(monkeypatch, pid=None):
    """Neither an existing registration nor a harness that claims an ancestor.

    Stubbed rather than assumed: on a developer's machine the test runner is a
    descendant of a real Claude session, so an unstubbed resolution answers
    differently here than on CI.
    """
    monkeypatch.setattr(agents_cmd.store, "get_self", lambda home=None: None)
    entry = None if pid is None else type("E", (), {"pid": pid})()
    monkeypatch.setattr(
        agents_cmd.store, "session_entry_for_current_process",
        lambda home=None: entry,
    )


def test_host_pid_resolves_the_session_this_command_runs_inside(bus, monkeypatch):
    """The whole of #118. Without this branch the CLI claims its own pid, the
    command exits, and the entry is pruned before anyone reads the roster."""
    _no_session(monkeypatch, pid=4242)
    assert agents_cmd.resolve_host_pid(None, bus) == (4242, agents_cmd.PID_SESSION)


def test_host_pid_falls_back_to_our_own(bus, monkeypatch):
    """Kept for the library caller -- omp imports agent_bus into a kernel that
    outlives the call, where our own pid is the right answer. The source is
    returned so the CLI, for which it never is, can refuse."""
    _no_session(monkeypatch)
    assert agents_cmd.resolve_host_pid(None, bus) == (os.getpid(), agents_cmd.PID_OWN)


def test_register_refuses_rather_than_claiming_a_pid_that_dies_with_it(
    bus, monkeypatch, capsys
):
    """It used to print "registered as x" and write nothing that survived."""
    _no_session(monkeypatch)
    assert main(["register", "--name", "doomed", "--kind", "other"]) == 1
    err = capsys.readouterr().err
    assert "--pid" in err
    assert load_roster(bus) == []


def test_register_with_no_flags_claims_the_session_not_the_command(
    bus, holder, monkeypatch, capsys
):
    """`agent-bus register --name x` from a shell is the documented gesture and
    the one nothing exercised: every test and every prompt passed --pid."""
    _no_session(monkeypatch, pid=holder.pid)
    assert main(["register", "--name", "mine", "--kind", "claude"]) == 0
    capsys.readouterr()
    assert [(e.name, e.pid) for e in load_roster(bus)] == [("mine", holder.pid)]


def test_registering_twice_renames_rather_than_duplicating(bus, holder, capsys):
    """A second register() renames the entry the first one made.

    The pid is explicit because the property under test is the rename. It used
    to be left to resolution, which passed only because pytest is long-lived --
    in a shell the first registration's pid is dead by the second call.
    """
    pid = str(holder.pid)
    assert main(["register", "--name", "first", "--kind", "omp", "--pid", pid]) == 0
    assert main(["register", "--name", "second", "--kind", "omp", "--pid", pid]) == 0
    capsys.readouterr()

    roster = load_roster(bus)
    assert [e.name for e in roster] == ["second"]
    assert len({e.id for e in roster}) == 1


def test_register_keeps_the_published_socket_name_in_step(bus, monkeypatch):
    """Only the MCP path did this, so a CLI rename left the socket stale --
    the name a sender read from the listing was not the name that worked."""
    seen = []
    monkeypatch.setattr(agents_cmd, "rename_uds_listen",
                        lambda pid, name, home=None: seen.append((pid, name)) or True)
    entry = agents_cmd.register("renamed", "omp", pid=os.getpid(), home=bus)
    assert seen == [(os.getpid(), entry["name"])]


# --- status reaches both places, and neither surface invents a failure ----

def test_status_recorded_on_roster_without_a_listener(bus, capsys):
    store_register("me", "other", pid=os.getpid(), home=bus)
    result = _tool("set_status", {"status": "busy"})
    assert result == {"recorded": True, "published": False, "status": "busy"}

    assert main(["status", "waiting"]) == 0
    assert "visible on the bus only" in capsys.readouterr().out
    assert [e.status for e in load_roster(bus)] == ["waiting"]


def test_status_when_not_registered_is_reported_not_raised(bus, capsys):
    result = _tool("set_status", {"status": "busy"})
    assert result["recorded"] is False
    assert main(["status", "busy"]) == 1
    assert "not registered" in capsys.readouterr().err


# --- the paths a human actually types ------------------------------------

def test_text_output_paths_render(bus, holder, capsys):
    """list/inbox/self without --json read the same dicts by key.

    The rewrite turned attribute access (`a.name`, `m["from_"].name`) into
    dict access, and every parity test above goes through --json -- so a
    KeyError in the human-readable output would ship past the whole suite.
    """
    store_register("sender", "other", pid=os.getpid(), home=bus)
    store_register("target", "other", pid=holder.pid, home=bus)
    main(["send", "target", "-m", "body text", "--summary", "sum", "--from-name", "sender"])
    capsys.readouterr()

    assert main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "target" in listed and "NAME" in listed

    assert main(["inbox", "--name", "target"]) == 0
    shown = capsys.readouterr().out
    # Who sent it, not what they are running -- the harness is in --json,
    # and a reader replies to a name rather than to a kind.
    assert "from sender" in shown and "unread" in shown
    assert "from sender: sum" in shown, "the summary is the subject line"
    assert "body text" in shown

    assert main(["self"]) == 0
    assert "sender (other)" in capsys.readouterr().out


def test_empty_text_output_paths_render(bus, capsys):
    """`list` is never reliably empty -- it unions the roster with natively
    discovered sessions, and the machine running the tests may have one. Use
    an unknown kind filter to force the empty branch."""
    assert main(["list", "--kind", "no-such-harness"]) == 0
    assert "no agents" in capsys.readouterr().out
    store_register("solo", "other", pid=os.getpid(), home=bus)
    assert main(["inbox"]) == 0
    assert "no messages" in capsys.readouterr().out
