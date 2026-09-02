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

from roster import found

from agent_bus.protocol import AgentTarget, MessageId
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
        send_message(to=AgentTarget("recipient"), text="queued while alive", from_name=AgentTarget(
            "sender",
        ), home=home)
    finally:
        holder.kill()
        holder.wait()

    prune_dead_roster(home)

    assert has_mail(found(AgentTarget("recipient"), home=home).id, home=home)
    entry = find_entry(AgentTarget("recipient"), home=home)
    assert entry is not None, "an agent with undelivered mail must stay addressable"
    assert entry.name == "recipient"


def test_dead_agent_with_mail_is_not_in_the_live_roster(tmp_path):
    """Addressable is not the same as present: it must not show up as live."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("gone", "other", pid=holder.pid, home=home)
        send_message(to=AgentTarget("gone"), text="mail", from_name=AgentTarget(
            "sender",
        ), home=home)
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
    assert find_entry(AgentTarget("ephemeral"), home=home) is None


def test_send_to_a_dead_agent_still_delivers(tmp_path):
    """The failure this whole change exists to fix: replying to an agent that
    has just exited used to raise "no such agent"."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("offline-peer", "other", pid=holder.pid, home=home)
        send_message(to=AgentTarget("offline-peer"), text="first", from_name=AgentTarget(
            "s",
        ), home=home)
    finally:
        holder.kill()
        holder.wait()

    prune_dead_roster(home)
    mid = send_message(
        to=AgentTarget("offline-peer"), text="after it exited",
        from_name=AgentTarget("s"), home=home,
    )
    assert mid


def test_live_entry_wins_over_a_stale_one_with_the_same_name(tmp_path):
    """A restarted agent reusing a name must win, or messages go to the corpse."""
    home = str(tmp_path)
    # the stale entry must have mail, or pruning removes it before we can test
    old = subprocess.Popen(["sleep", "30"])
    register("twin", "other", pid=old.pid, home=home)
    send_message(to=AgentTarget("twin"), text="keeps the stale entry alive", from_name=AgentTarget(
        "s",
    ), home=home)
    old.kill()
    old.wait()

    live = subprocess.Popen(["sleep", "30"])
    try:
        register("twin", "other", pid=live.pid, home=home)
        entry = find_entry(AgentTarget("twin"), home=home)
        assert entry is not None and entry.pid == live.pid
    finally:
        live.kill()
        live.wait()


# ------------------------------------------------------------------ liveness


def test_liveness_rejects_a_recycled_pid(tmp_path):
    """A pid alone is not identity. With a recorded start time that does not
    match the running process, the agent is dead however alive the pid looks.

    The mismatched value is derived from the real one rather than written out.
    Two start times are only comparable when they are the same format, and
    which format a machine produces depends on whether it has /proc -- a
    literal here asserts "dead" on one platform and "cannot tell" on the other.
    """
    from agent_bus.process import proc_start

    start = proc_start(1)
    assert start is not None, "pid 1 has no start time to perturb"
    assert is_process_alive(1, start + "9") is False


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


# ------------------------------------------------ regressions from PR #9 review


def test_graceful_shutdown_also_keeps_mail(tmp_path):
    """unregister_by_pid is the clean SessionEnd path and bypassed retention
    entirely, so an agent that exited *cleanly* with mail waiting still became
    unreachable -- the exact failure retention exists to prevent."""
    from agent_bus.store import unregister_by_pid

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("clean-exit", "other", pid=holder.pid, home=home)
        send_message(to=AgentTarget("clean-exit"), text="queued", from_name=AgentTarget(
            "s",
        ), home=home)
    finally:
        holder.kill()
        holder.wait()

    unregister_by_pid(holder.pid, home=home)
    assert find_entry(AgentTarget("clean-exit"), home=home) is not None
    assert send_message(to=AgentTarget("clean-exit"), text="still reachable", from_name=AgentTarget(
        "s",
    ), home=home)


def test_graceful_shutdown_still_removes_an_empty_agent(tmp_path):
    from agent_bus.store import unregister_by_pid

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    register("nothing-waiting", "other", pid=holder.pid, home=home)
    holder.kill()
    holder.wait()
    unregister_by_pid(holder.pid, home=home)
    assert find_entry(AgentTarget("nothing-waiting"), home=home) is None


def test_an_acked_inbox_counts_as_empty(tmp_path):
    """ack rewrites read:true rather than deleting, so file size never returns
    to zero -- retention on size kept every agent that ever got a message."""
    from agent_bus.store import ack_message, get_inbox

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("reader", "other", pid=holder.pid, home=home)
        send_message(to=AgentTarget("reader"), text="read me", from_name=AgentTarget(
            "s",
        ), home=home)
        entry_id = found(AgentTarget("reader"), home=home).id
        assert has_mail(entry_id, home=home)
        for m in get_inbox(AgentTarget("reader"), home=home):
            ack_message(MessageId(m["id"]), target=AgentTarget("reader"), home=home)
        assert not has_mail(entry_id, home=home), "acked mail is not undelivered mail"
    finally:
        holder.kill()
        holder.wait()


def test_a_recycled_pid_cannot_inherit_a_dead_agents_mail(tmp_path):
    """The chain the review found: retained dead entry + adopt-on-bare-pid meant
    a recycled pid inherited the dead entry's id and read its queued mail."""
    from agent_bus.store import get_inbox

    home = str(tmp_path)
    victim = subprocess.Popen(["sleep", "30"])
    victim_id = register("victim", "other", pid=victim.pid, home=home).id
    send_message(to=AgentTarget("victim"), text="secret for victim", from_name=AgentTarget(
        "s",
    ), home=home)
    victim.kill()
    victim.wait()

    # a new agent registering under the same (now recycled) pid
    entry = register("newcomer", "other", pid=victim.pid, home=home)
    assert entry.name == "newcomer"
    assert entry.id != victim_id, "must not inherit the dead agent's identity"

    # It must not read the victim's mail. The newcomer's own pid is dead too,
    # so it is not addressable at all -- assert on the secret either way rather
    # than letting an empty list pass for a security property.
    try:
        texts = [m["text"] for m in get_inbox(AgentTarget("newcomer"), home=home)]
    except ValueError:
        texts = []
    assert "secret for victim" not in texts, texts

    # And the victim's mail is still there, under the victim's own address.
    victim_texts = [m["text"] for m in get_inbox(AgentTarget(victim_id), home=home)]
    assert victim_texts == ["secret for victim"]


def test_proc_start_survives_a_disk_round_trip(tmp_path):
    """Without serialization the pid-reuse guard is inert everywhere, because
    every call site reads entries back from load_roster()."""
    from agent_bus.store import load_roster

    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        entry = register("persisted", "other", pid=holder.pid, home=home)
        assert entry.procStart, "registration should record it"
        loaded = next(e for e in load_roster(home) if e.name == "persisted")
        assert loaded.procStart == entry.procStart
    finally:
        holder.kill()
        holder.wait()


def test_adopting_an_entry_refreshes_proc_start(tmp_path):
    """Never inherit: persisting the previous holder's start time onto a live
    registrant gives the entry a provably wrong identity."""
    home = str(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        first = register("adopter", "other", pid=holder.pid, home=home)
        second = register("adopter-renamed", "other", pid=holder.pid, home=home)
        assert second.id == first.id, "same pid should adopt, not duplicate"
        assert second.procStart == first.procStart  # same live process
        assert second.procStart is not None
    finally:
        holder.kill()
        holder.wait()
