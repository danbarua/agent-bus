"""Presence and mailbox are different lifetimes.

An entry is both a presence record and the only pointer to a mailbox. Deleting
it when the process exits threw the mailbox away with it: a reply to an agent
that had just exited failed with "no such agent", and queued mail became
unreachable. Correct only if a peer is by definition a live socket -- true of
Claude, false of a Codex thread, which is addressable precisely because nothing
is running.
"""

import subprocess
import sys

from agent_bus.store import (
    find_entry,
    get_live_roster,
    has_mail,
    is_process_alive,
    prune_dead_roster,
    register,
    send_message,
)


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_dead_agent_with_mail_stays_addressable(tmp_path):
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("recipient", "other", pid=holder.pid, home=home)
        send_message(to="recipient", text="queued while alive", from_name="sender", home=home)
    finally:
        holder.kill()
        holder.wait()

    prune_dead_roster(home)

    assert has_mail(find_entry("recipient", home=home).id, home=home)
    entry = find_entry("recipient", home=home)
    assert entry is not None, "an agent with undelivered mail must stay addressable"
    assert entry.name == "recipient"


def test_dead_agent_with_mail_is_not_in_the_live_roster(tmp_path):
    """Addressable is not the same as present: it must not show up as live."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("gone", "other", pid=holder.pid, home=home)
        send_message(to="gone", text="mail", from_name="sender", home=home)
    finally:
        holder.kill()
        holder.wait()

    assert [e.name for e in get_live_roster(home)] == []


def test_dead_agent_without_mail_is_pruned(tmp_path):
    """Nothing to preserve, so presence is dropped as before -- otherwise the
    roster grows forever."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("ephemeral", "other", pid=holder.pid, home=home)
    finally:
        holder.kill()
        holder.wait()

    prune_dead_roster(home)
    assert find_entry("ephemeral", home=home) is None


def test_send_to_a_dead_agent_still_delivers(tmp_path):
    """The failure this whole change exists to fix: replying to an agent that
    has just exited used to raise "no such agent"."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("offline-peer", "other", pid=holder.pid, home=home)
        send_message(to="offline-peer", text="first", from_name="s", home=home)
    finally:
        holder.kill()
        holder.wait()

    prune_dead_roster(home)
    mid = send_message(to="offline-peer", text="after it exited", from_name="s", home=home)
    assert mid


def test_live_entry_wins_over_a_stale_one_with_the_same_name(tmp_path):
    """A restarted agent reusing a name must win, or messages go to the corpse."""
    home = str(tmp_path)
    # the stale entry must have mail, or pruning removes it before we can test
    old = subprocess.Popen(["sleep", "30"])
    register("twin", "other", pid=old.pid, home=home)
    send_message(to="twin", text="keeps the stale entry alive", from_name="s", home=home)
    old.kill()
    old.wait()

    live = subprocess.Popen(["sleep", "30"])
    try:
        register("twin", "other", pid=live.pid, home=home)
        entry = find_entry("twin", home=home)
        assert entry is not None and entry.pid == live.pid
    finally:
        live.kill()
        live.wait()


# ------------------------------------------------------------------ liveness


def test_liveness_rejects_a_recycled_pid(tmp_path):
    """A pid alone is not identity. With a recorded start time that does not
    match the running process, the agent is dead however alive the pid looks."""
    assert is_process_alive(1, "Thu Jan  1 00:00:00 1970") is False


def test_liveness_falls_back_when_start_time_is_unknown(tmp_path):
    """Entries written before procStart existed must not all read as dead."""
    live = subprocess.Popen(["sleep", "30"])
    try:
        assert is_process_alive(live.pid, None) is True
    finally:
        live.kill()
        live.wait()


def test_liveness_is_false_for_a_dead_pid():
    assert is_process_alive(_dead_pid(), None) is False
