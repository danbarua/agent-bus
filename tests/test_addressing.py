"""The address spaces, and the sparseness that justifies a fourth axis."""
import subprocess

import pytest

from agent_bus.address import BUS, PID, SESSION, THREAD
from agent_bus.adapters import addressing
from agent_bus.adapters.contracts import AddressSpace


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def _dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


@pytest.mark.parametrize("mod", addressing.ADAPTERS, ids=lambda m: m.SPACE)
def test_every_space_satisfies_the_contract(mod):
    assert isinstance(mod, AddressSpace)


def test_the_spaces_are_what_we_say_they_are():
    assert {m.SPACE for m in addressing.ADAPTERS} == {BUS, SESSION, PID, THREAD}


def test_thread_is_the_only_space_without_a_liveness_rule():
    """If this stops being true the fourth axis has stopped paying for itself."""
    dead = {"pid": _dead_pid(), "procStart": None, "kind": "x"}
    by_space = {}
    for mod in addressing.ADAPTERS:
        by_space[mod.SPACE] = mod.is_live({**dead, "id": f"x:{mod.SPACE}:v"})
    assert by_space == {BUS: False, SESSION: False, PID: False, THREAD: True}


def test_a_thread_is_live_with_no_process_at_all():
    """Verified against a real app-server: every thread reports notLoaded and
    every one accepts a queued message anyway."""
    assert addressing.is_live({"id": "codex:thread:abc", "kind": "codex", "pid": None})


def test_a_process_backed_address_needs_its_process(holder):
    live = {"id": "grok:sid-1", "kind": "grok", "pid": holder.pid, "procStart": None}
    assert addressing.is_live(live) is True
    assert addressing.is_live({**live, "pid": _dead_pid()}) is False


@pytest.mark.parametrize("entry,expected", [
    ({"id": "codex:thread:abc", "kind": "codex"}, False),
    ({"id": "claude:sid-1", "kind": "claude"}, False),
    ({"id": "grok:sid-1", "kind": "grok"}, True),
    ({"id": "omp:tty:42", "kind": "omp"}, True),
    ({"id": "8054898a-uuid", "kind": "other"}, True),
])
def test_which_addresses_may_be_written_to(entry, expected):
    """A Claude peer has no inbox and never polls one, so filing a message for
    it leaves an unread nobody can clear -- how four inboxes here were orphaned."""
    assert addressing.has_mailbox(entry) is expected


def test_an_unknown_space_behaves_as_addresses_did_before_spaces_existed():
    """A harness we have not heard of must not vanish from the bus."""
    entry = {"id": "weird:notaspace:v", "kind": "weird", "pid": None}
    assert addressing.for_entry(entry) is addressing.DEFAULT
    assert addressing.has_mailbox(entry) is True


def test_tty_is_routed_to_the_pid_space():
    assert addressing.for_entry({"id": "omp:tty:42", "kind": "omp"}).SPACE == PID


def test_spaces_read_dataclass_entries_as_well_as_dicts(holder):
    from agent_bus.store import register
    import tempfile
    with tempfile.TemporaryDirectory() as home:
        entry = register("x", "grok", pid=holder.pid, home=home)
        assert addressing.is_live(entry) is True
        assert addressing.has_mailbox(entry) is True
