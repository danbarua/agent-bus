"""agent-bus watch: the wake source for a harness that can watch but has
nothing to watch.

Grok's monitor tool turns each stdout line into a conversation event, and its
limits dictate this output: a token bucket of 10 refilling one per 2s, auto-kill
after 30s of suppression, 500 chars a line, and exit ends the watch.
"""

import io
import subprocess

from agent_bus.protocol import AgentTarget
from agent_bus.store import register, send_message
from agent_bus.watch import MAX_LINE, format_event, watch


def _agent(home, name="watcher"):
    holder = subprocess.Popen(["sleep", "60"])
    register(name, "other", pid=holder.pid, home=str(home))
    return holder


def test_one_line_per_message(tmp_path):
    holder = _agent(tmp_path)
    try:
        send_message(
            to=AgentTarget("watcher"), text="first", summary="first",
            from_name=AgentTarget("a"), home=str(tmp_path),
        )
        send_message(
            to=AgentTarget("watcher"), text="second", summary="second", from_name=AgentTarget("b"),
            home=str(tmp_path),
        )
        out = io.StringIO()
        watch(AgentTarget("watcher"), home=str(tmp_path), from_start=True, once=True, out=out)
    finally:
        holder.kill()
        holder.wait()
    lines = out.getvalue().splitlines()
    assert len(lines) == 2, lines
    assert "from=a" in lines[0] and "from=b" in lines[1]


def test_starts_from_now_by_default(tmp_path):
    """Replaying a backlog is the fastest way to trip a monitor's rate limiter
    and be auto-killed in the first second."""
    holder = _agent(tmp_path)
    try:
        for i in range(20):
            send_message(
                to=AgentTarget("watcher"), text=f"old {i}",
                from_name=AgentTarget("a"), home=str(tmp_path),
            )
        out = io.StringIO()
        watch(AgentTarget("watcher"), home=str(tmp_path), once=True, out=out)
        assert out.getvalue() == "", "an existing backlog must not be replayed"
    finally:
        holder.kill()
        holder.wait()


def test_new_messages_are_emitted_after_the_starting_offset(tmp_path):
    holder = _agent(tmp_path)
    try:
        send_message(
            to=AgentTarget("watcher"), text="before", from_name=AgentTarget("a"),
            home=str(tmp_path),
        )
        out = io.StringIO()
        watch(AgentTarget("watcher"), home=str(tmp_path), once=True, out=out)   # consumes nothing
        send_message(
            to=AgentTarget("watcher"), text="after", summary="after", from_name=AgentTarget("b"),
            home=str(tmp_path),
        )
        # a second pass from the end still sees nothing; from_start sees both
        full = io.StringIO()
        watch(AgentTarget("watcher"), home=str(tmp_path), from_start=True, once=True, out=full)
    finally:
        holder.kill()
        holder.wait()
    assert len(full.getvalue().splitlines()) == 2


def test_line_carries_sender_and_id_and_stays_bounded():
    line = format_event({
        "id": "5c6c39e9-4d48-4d40-abea-8b1265163d44",
        "from": {"name": "claude-bus"},
        "summary": "x" * 500,
        "text": "y" * 5000,
    })
    assert "from=claude-bus" in line
    assert "id=5c6c39e9" in line
    assert len(line) <= MAX_LINE, len(line)


def test_body_is_never_included_whole():
    line = format_event({"id": "1", "from": {"name": "a"}, "text": "SECRET" * 200})
    assert len(line) <= MAX_LINE


def test_newlines_in_a_summary_cannot_forge_extra_events():
    """One line per message is the contract: a message whose summary contains
    newlines must not become several monitor events."""
    line = format_event({
        "id": "1",
        "from": {"name": "a"},
        "summary": "line one\nline two\nline three",
    })
    assert "\n" not in line


def test_partial_trailing_record_is_not_consumed(tmp_path):
    """The inbox is appended to by another process; half a line must wait."""
    from agent_bus.watch import _read_records

    p = tmp_path / "inbox.jsonl"
    p.write_text('{"id":"1","from":{"name":"a"},"summary":"ok"}\n{"id":"2","fro')
    records, offset = _read_records(str(p), 0)
    assert [r["id"] for r in records] == ["1"]
    rest, _ = _read_records(str(p), offset)
    assert rest == []


def test_unresolvable_agent_reports_rather_than_crashing(tmp_path):
    assert watch(AgentTarget("nobody-here"), home=str(tmp_path), once=True) == 1
