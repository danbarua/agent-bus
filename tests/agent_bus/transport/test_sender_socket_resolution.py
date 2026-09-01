"""Which socket a send claims as its own return address (#182).

The frame's `from` is what the recipient dials back to ack, so getting it
wrong does not fail -- it succeeds and routes the reply to whoever owns the
socket we named. That is worse than an error, and it is what the removed
step-4 fallback did: "the single live listener in this AGENT_BUS_HOME is
unambiguously ours", true on the one-agent machine it was written on and
false on the shared home agent-bus exists for.

Measured before the fix: eleven agents shared one home, exactly one published
a listener, and it was the desktop bridge -- so an unregistered sender's
replies were routed to the component whose job is relaying off the machine.
Not a random stranger: a Claude session publishes its own socket and writes no
listeners/<pid>.pid, so the only peers that can be "the single live listener"
are the ones that run `join`.

The replacement is the step that should always have been there: a session's
OWN published socket, found by walking ancestors in the same directory with
the same naming, just not written by us.
"""


import pytest

from agent_bus import uds


@pytest.fixture
def sock_dir(tmp_path, monkeypatch):
    d = tmp_path / "s"
    d.mkdir()
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", str(d))
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path / "bus"))
    (tmp_path / "bus" / "listeners").mkdir(parents=True)
    return d


def _resolve(monkeypatch, ancestors):
    """Which socket the resolution decides is ours, and nothing else."""
    monkeypatch.setattr(uds, "ancestor_pids", lambda start=None: ancestors)
    return uds._our_socket()


def test_an_ancestors_own_socket_is_used(sock_dir, monkeypatch):
    """The case that fell through to the bad fallback: a Claude session whose
    socket Claude published, not us.

    The ancestor is a real other process on purpose. Using os.getpid() here
    passes through step 2 -- `<sock_dir>/<our pid>.sock`, we are the listener
    -- without ever reaching step 4, so it would go green with step 4 deleted.
    Caught by mutation; the pid has to be one only the ancestor walk can find.
    """
    import subprocess

    ancestor = subprocess.Popen(["sleep", "30"])
    try:
        (sock_dir / f"{ancestor.pid}.sock").write_text("")
        assert _resolve(monkeypatch, [ancestor.pid]) == str(
            sock_dir / f"{ancestor.pid}.sock"
        )
    finally:
        ancestor.kill()
        ancestor.wait()


def test_a_stale_socket_is_not_claimed(sock_dir, monkeypatch):
    """/tmp/cc-socks holds dozens of sockets whose processes are long gone, so
    existence alone cannot mean ours."""
    (sock_dir / "999999.sock").write_text("")
    assert _resolve(monkeypatch, [999999]) is None


def test_another_agents_listener_is_never_claimed(sock_dir, monkeypatch, tmp_path):
    """#182 itself. One live listener on the bus, owned by somebody else, and
    the sender is not among its ancestors -- the exact shape that routed
    replies to the desktop bridge."""
    import subprocess

    holder = subprocess.Popen(["sleep", "30"])
    try:
        (sock_dir / f"{holder.pid}.sock").write_text("")
        (tmp_path / "bus" / "listeners" / "4242.pid").write_text(str(holder.pid))
        # ancestors deliberately exclude the holder: we are not its descendant
        assert _resolve(monkeypatch, [11111, 22222]) is None, (
            "a live listener nobody linked to us was claimed as our own return "
            "address -- replies would go to its owner"
        )
    finally:
        holder.kill()
        holder.wait()
