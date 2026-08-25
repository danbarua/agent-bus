"""The `desktop` peer class: Claude Desktop and ChatGPT.

A desktop peer is not a process and never wakes. It is reached by a *bridge*
process -- one per provider -- that registers on the bus as an ordinary peer and
carries mail to and from a public HTTPS service.

That choice is what makes this module short. The design doc originally gave
`desktop` its own address space with existence-only liveness; a bridge needs
none of it, because registering already supplies an address, a mailbox, liveness
and pruning. What is left is the one fact registration cannot supply: that a
human, not a loop, is what makes a desktop peer read its mail.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_bus import address, protocol, store


@pytest.fixture
def bus(tmp_path):
    return str(tmp_path / "bus")


@pytest.fixture
def holder():
    """A live pid to register against, standing in for a running bridge."""
    p = subprocess.Popen(["sleep", "30"])
    yield p
    p.kill()
    p.wait()


def _bridge(bus, holder, provider="claude"):
    """Register as `agent-bridge --provider <p>` does."""
    return store.register(
        f"desktop-{provider}",
        "desktop",
        pid=holder.pid,
        home=bus,
        aliases=[f"desktop:{provider}"],
    )


# ------------------------------------------------------------------ the kind

def test_desktop_is_a_known_kind():
    """A product decision, made explicitly -- not a harness we discovered."""
    assert "desktop" in protocol.KNOWN_KINDS


# ------------------------------------------------------- delivery expectation

def test_a_desktop_peer_is_queued_and_everything_else_is_now():
    assert protocol.delivery_expectation("desktop") == protocol.QUEUED
    for kind in ("claude", "grok", "omp", "codex", "other", None):
        assert protocol.delivery_expectation(kind) == protocol.NOW, kind


def test_delivery_expectation_normalizes_its_input():
    assert protocol.delivery_expectation("  DESKTOP ") == protocol.QUEUED


def test_the_two_expectations_are_actually_different():
    """Guard against both constants collapsing to one string in a refactor --
    the auto-reply's whole job is to say different things, and it cannot if
    these are equal."""
    assert protocol.NOW != protocol.QUEUED


# ----------------------------------------------------------- the address trap

def test_a_bridge_gets_a_bus_address_not_a_session_one(bus, holder):
    """The assertion that pins the trap shut.

    `address.parse` hands *any* two-part id the SESSION space, whose liveness is
    a harness process id. Had `desktop:claude` been minted as an id it would
    have parsed as a live process that does not exist, and been pruned as dead.

    A bridge registers normally, so its id is the bare uuid register() mints --
    the BUS space, process-backed, correct. Nothing parses the pretty spelling.
    """
    entry = _bridge(bus, holder)
    assert address.parse(entry.id, kind_hint=entry.kind).space == address.BUS


def test_the_trap_is_real_so_the_test_above_is_not_vacuous():
    """Verify the guard by watching it fail: if this ever stops holding, the
    assertion above has stopped meaning anything."""
    assert address.parse("desktop:claude").space == address.SESSION


def test_the_pretty_spelling_resolves_as_an_alias(bus, holder):
    entry = _bridge(bus, holder)
    found = store.find_entry("desktop:claude", home=bus)
    assert found is not None and found.id == entry.id


def test_a_running_bridge_survives_pruning(bus, holder):
    entry = _bridge(bus, holder)
    store.prune_dead_roster(bus)
    assert store.find_entry(entry.name, home=bus) is not None


def test_a_dead_bridge_is_not_live(bus, holder):
    """Liveness is true rather than assumed: a desktop peer is reachable exactly
    when its bridge is running. Existence-only liveness would have claimed a
    dead bridge was fine."""
    from agent_bus.adapters import addressing

    entry = _bridge(bus, holder)
    assert addressing.is_live(entry) is True
    holder.kill()
    holder.wait()
    assert addressing.is_live(entry) is False


# ------------------------------------------------------------------- routing

def test_desktop_has_no_native_transport(bus, holder):
    """No cloud adapter exists, and that is the design rather than an omission:
    mail for a desktop peer takes the plain file-bus path, and the bridge drains
    it. `commands.messages.send` needs no branch for desktop."""
    from agent_bus.adapters import transport

    assert transport.for_kind("desktop") is None


def test_mail_for_a_desktop_peer_waits_unread(bus, holder):
    """The distinction the pre-acked copy exists to preserve. A Claude peer's
    inbox record is written already-read because delivery happened elsewhere; a
    desktop peer's is genuinely waiting, and must stay unread until its bridge
    takes it."""
    from agent_bus.commands import messages

    entry = _bridge(bus, holder)
    messages.send(to=entry.name, text="ask the desktop", from_name="s", home=bus)

    unread = store.get_inbox(entry.name, unread_only=True, home=bus)
    assert [m["text"] for m in unread] == ["ask the desktop"]
