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
    set_status,
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


def test_a_reconnect_under_the_same_name_takes_over_the_old_entry(tmp_path):
    """No live process holds a dead-with-mail entry's old pid, and a fresh
    registration under its exact name arrives -- the shape a resumed session
    has from here, pid changed underneath it, same identity. register() takes
    the existing entry over (same id, same inbox) rather than minting a
    second one: mail already queued for it is still reachable afterward, and
    there is only ever one 'twin' row, not two.
    """
    home = str(tmp_path)
    old = subprocess.Popen(["sleep", "30"])
    register("twin", "omp", pid=old.pid, home=home)
    original = find_entry(AgentTarget("twin"), home=home)
    assert original is not None
    set_status("busy", target=AgentTarget("twin"), home=home)
    send_message(to=AgentTarget("twin"), text="queued before the reconnect",
                 from_name=AgentTarget("s"), home=home)
    old.kill()
    old.wait()

    resumed = subprocess.Popen(["sleep", "30"])
    try:
        register("twin", "omp", pid=resumed.pid, home=home)
        entry = find_entry(AgentTarget("twin"), home=home)
        assert entry is not None
        assert entry.id == original.id, "a reconnect must keep the original id"
        assert entry.pid == resumed.pid
        assert entry.status == "idle", (
            "a new pid is a new process, not a continuation of whatever "
            "status the previous one last reported"
        )
        assert has_mail(entry.id, home=home), (
            "mail queued before the reconnect must still be there after it"
        )
        assert len(get_live_roster(home)) == 1, "one row, not a duplicate"
    finally:
        resumed.kill()
        resumed.wait()


def test_a_live_thread_entry_is_not_taken_over(tmp_path):
    """A Codex thread is always live (adapters/addressing/thread.py) despite
    carrying no pid. register()'s reconnect-takeover branch must ask
    addressing.is_live, not a bare pid check -- otherwise a live, pid-less
    thread looks exactly like a dead entry and an unrelated registration
    under its exact name overwrites its identity.
    """
    from agent_bus.protocol import MailboxRef, RosterEntry
    from agent_bus.store import load_roster, save_roster_entry

    home = str(tmp_path)
    thread = RosterEntry(
        id=MailboxRef("codex:thread:abc123"),
        name="reviewer",
        kind="codex",
        pid=None,
        cwd=None,
        status="unknown",
        inbox="",
        native={},
        registeredAt="2026-01-01T00:00:00+00:00",
        updatedAt="2026-01-01T00:00:00+00:00",
    )
    save_roster_entry(thread, home=home)

    holder = subprocess.Popen(["sleep", "30"])
    try:
        entry = register("reviewer", "claude", pid=holder.pid, home=home)
        assert entry.id != thread.id, (
            "an unrelated live process must not take over a live thread's identity"
        )
        assert entry.name != "reviewer", (
            "the name is live-claimed by the thread, so the fresh "
            "registration must suffix, not collide"
        )
        by_id = {e.id: e for e in load_roster(home)}
        assert by_id[thread.id].kind == "codex", "the thread's identity must be untouched"
        assert by_id[thread.id].pid is None
    finally:
        holder.kill()
        holder.wait()


def test_a_reconnect_never_creates_two_live_entries_with_the_same_name(tmp_path):
    """dead X (has mail) + a live X from an unrelated rename must not both
    exist after a third registration lands as X -- the takeover branch must
    refuse to adopt a name a live entry already holds, or `find_entry` has
    two rows to arbitrarily choose between and mail splits across inboxes.
    """
    home = str(tmp_path)

    # dead_x: registered, then killed but kept on disk by its queued mail.
    dead_holder = subprocess.Popen(["sleep", "30"])
    register("x", "omp", pid=dead_holder.pid, home=home)
    send_message(to=AgentTarget("x"), text="keeps dead x alive",
                 from_name=AgentTarget("s"), home=home)
    dead_holder.kill()
    dead_holder.wait()

    # A second process registers as "y", then re-registers as "x" -- the
    # same-pid branch only excludes *other live* names, so it doesn't see
    # dead x and lets the rename through, producing a live "x".
    renamer = subprocess.Popen(["sleep", "30"])
    try:
        register("y", "omp", pid=renamer.pid, home=home)
        live_x = register("x", "omp", pid=renamer.pid, home=home)
        assert live_x.name == "x"

        # A third, unrelated process registers as "x" too.
        third = subprocess.Popen(["sleep", "30"])
        try:
            third_entry = register("x", "omp", pid=third.pid, home=home)
            live_named_x = [e for e in get_live_roster(home) if e.name == "x"]
            assert len(live_named_x) == 1, (
                f"exactly one live entry may be named x, got {live_named_x}"
            )
            assert third_entry.id != live_x.id, (
                "the third registration must not have adopted the dead entry "
                "while a live one already holds the name"
            )
        finally:
            third.kill()
            third.wait()
    finally:
        renamer.kill()
        renamer.wait()


def test_a_takeover_refuses_a_dead_entry_of_a_different_kind(tmp_path):
    """id is deliberately inherited on a takeover -- same id, same inbox --
    but id also carries harness-specific meaning (a discovered-only omp
    entry's id names its inbox "omp:<session-id>"). A same-named dead entry
    of a *different* kind is coincidence, not a reconnect: adopting it would
    hand a claude registration an omp session's mailbox and its queued mail.
    """
    home = str(tmp_path)

    omp_holder = subprocess.Popen(["sleep", "30"])
    register("reviewer", "omp", pid=omp_holder.pid, home=home)
    send_message(to=AgentTarget("reviewer"), text="for the omp session",
                 from_name=AgentTarget("s"), home=home)
    omp_original = find_entry(AgentTarget("reviewer"), home=home)
    assert omp_original is not None
    omp_holder.kill()
    omp_holder.wait()

    claude_holder = subprocess.Popen(["sleep", "30"])
    try:
        claude_entry = register("reviewer", "claude", pid=claude_holder.pid, home=home)
        assert claude_entry.id != omp_original.id, (
            "a claude registration must not adopt a dead omp entry's id "
            "just because the name matches"
        )
        assert claude_entry.kind == "claude"
        assert has_mail(omp_original.id, home=home), (
            "the omp session's queued mail must still be reachable under "
            "its own id, not silently handed to the claude registration"
        )
    finally:
        claude_holder.kill()
        claude_holder.wait()


def test_a_takeover_normalizes_the_dead_entrys_kind(tmp_path):
    """The candidate match is already loose (normalize_kind both sides), so a
    dead entry stored non-canonically must not survive the takeover that way
    -- transport/lifecycle routing both compare kind raw, and a non-canonical
    value routes nowhere.
    """
    from agent_bus.store import load_roster, save_roster_entry

    home = str(tmp_path)
    dead_holder = subprocess.Popen(["sleep", "30"])
    register("reviewer", "omp", pid=dead_holder.pid, home=home)
    send_message(to=AgentTarget("reviewer"), text="keeps the dead entry on disk",
                 from_name=AgentTarget("s"), home=home)
    entry = find_entry(AgentTarget("reviewer"), home=home)
    assert entry is not None
    entry.kind = "Claude"
    save_roster_entry(entry, home=home)
    dead_holder.kill()
    dead_holder.wait()

    resumed = subprocess.Popen(["sleep", "30"])
    try:
        register("reviewer", "claude", pid=resumed.pid, home=home)
        by_id = {e.id: e for e in load_roster(home)}
        assert by_id[entry.id].kind == "claude"
    finally:
        resumed.kill()
        resumed.wait()


def test_a_takeover_does_not_inherit_the_dead_entrys_former_names(tmp_path):
    """A renamed then dead entry taken over by a reconnect must not hand the
    new process a second, unearned live name.

    find_entry resolves against _live_former_names too, so an inherited
    former name still inside its grace window becomes a live alias for
    whoever took the entry over. Sequence: A registers as "old", renames to
    "kept" (which records "old" as a former name), and dies with mail
    queued -- kept on disk. Because used_names only looks at *live* entries,
    a second process is free to register as "old" for real. A third process
    then registers as "kept": no live pid holds it, so it takes A's dead
    entry over. If that takeover kept "old" in formerNames, "old" would now
    resolve to two live entries -- the genuine one and the takeover.
    """
    home = str(tmp_path)

    a = subprocess.Popen(["sleep", "30"])
    register("old", "omp", pid=a.pid, home=home)
    register("kept", "omp", pid=a.pid, home=home)  # same-pid rename
    send_message(to=AgentTarget("kept"), text="keeps the entry alive",
                 from_name=AgentTarget("s"), home=home)
    a.kill()
    a.wait()

    b = subprocess.Popen(["sleep", "30"])
    try:
        genuine_old = register("old", "omp", pid=b.pid, home=home)
        assert genuine_old.name == "old"

        c = subprocess.Popen(["sleep", "30"])
        try:
            register("kept", "omp", pid=c.pid, home=home)

            resolved_old = find_entry(AgentTarget("old"), home=home)
            assert resolved_old is not None
            assert resolved_old.id == genuine_old.id, (
                "\"old\" must resolve to the process that is really named "
                "that, not to whatever took over \"kept\""
            )
        finally:
            c.kill()
            c.wait()
    finally:
        b.kill()
        b.wait()


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
