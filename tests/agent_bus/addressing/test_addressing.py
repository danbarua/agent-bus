"""The address spaces, and the sparseness that justifies a fourth axis."""
import subprocess

import pytest

from agent_bus.adapters import addressing
from agent_bus.adapters.contracts import AddressSpace
from agent_bus.address import BUS, SESSION, THREAD


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
    assert {m.SPACE for m in addressing.ADAPTERS} == {BUS, SESSION, THREAD}


def test_thread_is_the_only_space_without_a_liveness_rule():
    """If this stops being true the fourth axis has stopped paying for itself."""
    dead = {"pid": _dead_pid(), "procStart": None, "kind": "x"}
    by_space = {}
    for mod in addressing.ADAPTERS:
        by_space[mod.SPACE] = mod.is_live({**dead, "id": f"x:{mod.SPACE}:v"})
    assert by_space == {BUS: False, SESSION: False, THREAD: True}


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
    ({"id": "claude:sid-1", "kind": "claude"}, True),
    ({"id": "grok:sid-1", "kind": "grok"}, True),
    ({"id": "omp:tty:42", "kind": "omp"}, True),
    ({"id": "8054898a-uuid", "kind": "other"}, True),
])
def test_which_addresses_may_be_written_to(entry, expected):
    """Every session has a mailbox now, Claude included.

    Claude used to be False here: it never polls an inbox, so a message filed
    for one left an unread nobody could clear, which is how four inboxes on the
    maintainer's machine were orphaned. commands.messages.send now writes that
    copy already-acked once a native transport has delivered, so the unread
    cannot exist -- the objection is dissolved, not overruled.

    A codex *thread* is still False, and for a different reason: it is not a
    process and has no inbox of ours to poll."""
    assert addressing.has_mailbox(entry) is expected


def test_an_unknown_space_behaves_as_addresses_did_before_spaces_existed():
    """A harness we have not heard of must not vanish from the bus."""
    entry = {"id": "weird:notaspace:v", "kind": "weird", "pid": None}
    assert addressing.for_entry(entry) is addressing.DEFAULT
    assert addressing.has_mailbox(entry) is True


def test_tty_and_pid_ids_fall_back_to_the_default_space():
    """The dedicated `pid` space is retired -- it was a byte-for-byte
    duplicate of `bus` (both process-backed, both always mailbox=True), so
    `codex:pid:<n>` and `omp:tty:<n>` -- legacy shapes no adapter mints any
    more, but an id already on disk does not get to change shape
    retroactively -- now parse as an unrecognised space and get DEFAULT's
    rule, which behaves identically to what the dedicated pid space did."""
    assert addressing.for_entry({"id": "omp:tty:42", "kind": "omp"}) is addressing.DEFAULT
    assert addressing.for_entry({"id": "codex:pid:42", "kind": "codex"}) is addressing.DEFAULT


def test_spaces_read_dataclass_entries_as_well_as_dicts(holder):
    import tempfile

    from agent_bus.store import register
    with tempfile.TemporaryDirectory() as home:
        entry = register("x", "grok", pid=holder.pid, home=home)
        assert addressing.is_live(entry) is True
        assert addressing.has_mailbox(entry) is True
