"""A TRACE log explains every roster row.

Each registration writes one `register_decided` record naming the rule that
answered it. Whether an id was inherited from a dead entry, or a new one
minted, is read from that record and not inferred from the roster afterwards.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import sys

import pytest

from agent_bus import log, logevents
from agent_bus.lifecycle import SessionDescriptor, session_end, session_start
from agent_bus.protocol import AgentTarget
from agent_bus.store import register, send_message


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.fixture
def trace(tmp_path, monkeypatch):
    dest = tmp_path / "agent-bus.jsonl"
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "trace")
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(dest))
    log.configure(force=True)

    def records(message: str | None = None) -> list[dict]:
        out = []
        with contextlib.suppress(OSError), open(dest, encoding="utf-8") as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
        return [r for r in out if message is None or r["message"] == message]

    yield records
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)


def _leave_mail_for_a_dead_holder(home: str, name: str) -> tuple[str, int]:
    """Register `name` under a process, queue one message, then let it die."""
    holder = subprocess.Popen(["sleep", "30"])
    try:
        first = register(name, "other", pid=holder.pid, home=home)
        send_message(to=AgentTarget(name), text="queued", from_name=AgentTarget("sender"),
                     home=home)
    finally:
        holder.kill()
        holder.wait()
    return first.id, holder.pid


def test_taking_over_a_dead_entry_says_the_id_was_reused_and_why(tmp_path, trace):
    home = str(tmp_path)
    old_id, old_pid = _leave_mail_for_a_dead_holder(home, "labkit-dev")

    again = register("labkit-dev", "other", home=home)

    [rec] = [r for r in trace("register_decided") if r["decision"] == "took_over_dead_entry"]
    assert again.id == old_id
    assert rec["why"] == "dead_entry_under_name_and_kind"
    assert rec["reused_id"] is True
    assert rec["entry_id"] == old_id
    assert rec["holder_pid"] == old_pid
    assert rec["target_pid"] == again.pid
    assert rec["unread"] == 1
    assert rec["candidates"] == 1
    assert rec["requested"] == rec["final_name"] == "labkit-dev"


def test_a_new_id_says_there_was_no_dead_entry_to_take_over(tmp_path, trace):
    home = str(tmp_path)

    entry = register("labkit-dev", "other", home=home)

    [rec] = trace("register_decided")
    assert rec["decision"] == "minted"
    assert rec["why"] == "no_dead_entry_under_name_and_kind"
    assert rec["reused_id"] is False
    assert rec["entry_id"] == entry.id
    assert rec["candidates"] == 0


def test_a_dead_entry_without_mail_is_pruned_and_the_id_is_new(tmp_path, trace):
    home = str(tmp_path)
    gone = register("labkit-dev", "other", pid=_dead_pid(), home=home)

    again = register("labkit-dev", "other", home=home)

    assert again.id != gone.id
    [removed] = trace("roster_entry_removed")
    assert removed["decision"] == "pruned_dead"
    assert removed["entry_id"] == gone.id
    decided = trace("register_decided")[-1]
    assert (decided["decision"], decided["reused_id"]) == ("minted", False)


def test_a_name_a_live_entry_holds_is_suffixed_and_says_so(tmp_path, trace):
    home = str(tmp_path)
    _leave_mail_for_a_dead_holder(home, "labkit-dev")  # a dead entry, mail waiting
    holder = subprocess.Popen(["sleep", "30"])
    try:
        register("elsewhere", "other", pid=holder.pid, home=home)
        register("labkit-dev", "other", pid=holder.pid, home=home)  # a live one, same name

        third = register("labkit-dev", "other", home=home)
    finally:
        holder.kill()
        holder.wait()

    rec = trace("register_decided")[-1]
    assert third.name == "labkit-dev-2"
    assert rec["decision"] == "minted"
    assert rec["why"] == "name_held_by_live_entry"
    assert rec["candidates"] == 1
    assert rec["requested"] == "labkit-dev"
    assert rec["final_name"] == "labkit-dev-2"


def test_a_second_registration_on_one_pid_is_an_update_not_a_new_entry(tmp_path, trace):
    home = str(tmp_path)
    first = register("labkit-dev", "other", home=home)

    renamed = register("picked", "other", home=home)

    rec = trace("register_decided")[-1]
    assert renamed.id == first.id
    assert rec["decision"] == "same_pid_update"
    assert rec["why"] == "pid_already_registered"
    assert rec["previous_name"] == "labkit-dev"
    assert rec["final_name"] == "picked"
    assert "candidates" not in rec


def test_a_session_start_says_which_name_it_kept(tmp_path, trace):
    home = str(tmp_path)
    pid = subprocess.Popen(["sleep", "30"])
    try:
        desc = SessionDescriptor(kind="other", session_id=None, pid=pid.pid,
                                 cwd=str(tmp_path), name="labkit-dev")
        session_start(descriptor=desc, home=home)
        register("picked", "other", pid=pid.pid, home=home)

        session_start(descriptor=desc, home=home)
        session_end(descriptor=desc, home=home)
    finally:
        pid.kill()
        pid.wait()

    first, second = trace("session_start_resolved")
    assert first["decision"] == "used_descriptor_name"
    assert first["why"] == "no_live_entry_on_this_pid"
    assert second["decision"] == "kept_claimed_name"
    assert second["why"] == "held_name_is_not_the_derived_default"
    assert second["previous_name"] == "picked"
    assert second["requested"] == "labkit-dev"
    [ended] = trace("session_ended")
    assert ended["removed"] == 1


def test_a_message_is_measured_and_never_copied(tmp_path, trace):
    home = str(tmp_path)
    register("recipient", "other", home=home)

    mid = send_message(to=AgentTarget("recipient"), text="the secret payload",
                       from_name=AgentTarget("sender"), home=home)

    [rec] = trace("message_written")
    assert rec["trace_id"] == mid
    assert rec["text_len"] == len("the secret payload")
    assert "the secret payload" not in json.dumps(trace())


def test_every_registry_event_is_a_trace_event():
    import agent_bus.registry_events as mod

    mine = [c for c in logevents.all_events() if c.__module__ == mod.__name__]
    assert mine
    assert {c.level for c in mine} == {"trace"}
