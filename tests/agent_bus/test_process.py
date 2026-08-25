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
    """The guard the test above protects: same pid, different start time."""
    assert is_process_alive(os.getpid(), proc_start(os.getpid())) is True
    assert is_process_alive(os.getpid(), "Thu Jan  1 00:00:00 1970") is False
