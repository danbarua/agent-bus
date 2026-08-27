"""Liveness primitives, and the environment they quietly depend on.

Both tests here exist because of a container. Neither could fail on the
maintainer's laptop, which is exactly why they are worth having: a check that
cannot fail on the machine you develop on is a check nobody knows is broken.
"""

from __future__ import annotations

import os

import pytest

from agent_bus.process import is_pid_alive, is_process_alive, proc_start


def test_self_is_alive():
    assert is_pid_alive(os.getpid()) is True


@pytest.mark.parametrize("pid", [None, 0, -1])
def test_absent_or_nonsense_pids_are_dead(pid):
    assert is_pid_alive(pid) is False


def test_eperm_means_alive_not_dead(monkeypatch):
    """A process we may not signal still exists.

    `kill(pid, 0)` raising EPERM means the process is there and belongs to
    someone else. Treating that as dead prunes live agents -- invisible on a
    single-user laptop, immediate in a container where agents and bus run under
    different uids.
    """

    def _eperm(_pid, _sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", _eperm)
    assert is_pid_alive(4242) is True


def test_esrch_means_dead(monkeypatch):
    def _esrch(_pid, _sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", _esrch)
    assert is_pid_alive(4242) is False


def test_proc_start_is_readable_in_this_environment():
    """`ps -o lstart=` must work, or the pid-reuse guard is silently inert.

    is_process_alive() compares a recorded start time against the running
    process so a recycled pid cannot pass for a live agent. proc_start()
    swallows every failure and returns None, and is_process_alive() then falls
    back to the bare pid -- deliberately, so a platform without ps degrades
    rather than declaring live agents dead.

    The cost of that kindness is that a missing `ps` disables the guard with no
    symptom at all. Debian slim images ship no procps, so an image built without
    it would run the whole suite green with the guard switched off. This test is
    the alarm: it asserts the environment can actually answer the question.
    """
    started = proc_start(os.getpid())
    assert started, (
        "proc_start() returned nothing for our own pid, so the pid-reuse guard "
        "in is_process_alive() is inert. On Debian-based images install procps."
    )


def test_pid_reuse_guard_rejects_a_mismatched_start_time():
    """The guard the test above protects: same pid, different start time.

    The mismatched value is derived from the real one rather than written out,
    because the two are only comparable when they are the same format -- and
    which format this machine produces depends on whether it has /proc.
    """
    mine = proc_start(os.getpid())
    assert is_process_alive(os.getpid(), mine) is True
    assert is_process_alive(os.getpid(), mine + "9") is False


def test_a_clock_step_must_not_kill_every_agent_at_once(tmp_path):
    """The #83 failure, at the level it actually bit.

    Measured, not reasoned about: a 3-minute `date -s` step moved the `ps
    -o lstart=` of a process that had not restarted by exactly 3 minutes, while
    /proc/<pid>/stat field 22 was byte-identical. So on any machine whose clock
    corrects -- a freshly booted VM reaching NTP, which is every CI worker --
    every recorded start time stopped matching at once and every live agent was
    judged dead. It surfaced as a roster that was empty milliseconds after a
    successful register().

    On Linux the recorded value must therefore be ticks since boot, which a
    clock step cannot move. Elsewhere `ps` is all there is and this is a skip,
    not a failure -- macOS has no /proc and does not step its clock in CI.
    """
    if not os.path.isdir("/proc"):
        pytest.skip("no /proc: this platform has nothing but `ps` to offer")
    started = proc_start(os.getpid())
    assert started.startswith("boot:"), (
        f"proc_start returned {started!r}, which is a wall-clock time. A clock "
        "step will move it and take the whole roster with it."
    )
    assert started[len("boot:"):].isdigit()


def test_an_entry_recorded_in_the_other_format_is_not_proof_of_death():
    """Every entry already on disk holds a `ps` timestamp.

    Comparing those against ticks since boot would make every agent on every
    machine look dead the moment this change shipped -- inflicting, once and
    deliberately, the exact failure it exists to remove. Two formats that
    cannot be compared mean "cannot tell", which is alive, exactly as a missing
    value already did.
    """
    legacy = "Thu Jan  1 00:00:00 1970"
    ticks = "boot:12345"
    other = legacy if proc_start(os.getpid()).startswith("boot:") else ticks
    assert is_process_alive(os.getpid(), other) is True


def test_a_comm_with_spaces_and_brackets_does_not_break_the_parse(monkeypatch):
    """/proc/<pid>/stat field 2 is the executable name in parentheses, and it
    may contain both. Splitting from the left is the classic way to get this
    wrong; every field after it would shift and field 22 would be nonsense."""
    if not os.path.isdir("/proc"):
        pytest.skip("no /proc")
    from agent_bus import process as proc_mod

    # Field 3 is the state letter; fields 4..52 are numbered so that each
    # one's value IS its field number, and a misparse reads as the wrong number
    # rather than as something merely odd.
    fields = " ".join(str(i) for i in range(4, 53))
    fake = f"4242 (weird (name) with spaces) S {fields}\n"

    class _F:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return fake

    monkeypatch.setattr(proc_mod, "open", lambda *a, **k: _F(), raising=False)
    # rest[19] is field 22, and fields 3.. were numbered from 3 above.
    assert proc_mod._proc_start_linux(4242) == "boot:22"
