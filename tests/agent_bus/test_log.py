"""What the logger must never do, and the one thing it must always say.

Logging is the code most likely to be wrong in a way nobody notices: it runs
everywhere, its output is read only during an incident, and a mistake in it
looks like a mistake in whatever it was describing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys

import pytest

from agent_bus import log

REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@pytest.fixture
def logging_at(tmp_path, monkeypatch, capsys):
    """Configure the logger at a level, writing where this test can read it.

    Records go to a file rather than stderr, because that is where they go in
    life. `_records` reads whichever destination was configured.
    """
    written = tmp_path / "agent-bus.jsonl"

    def _at(level=None, file=None):
        for var, val in (("AGENT_BUS_LOG_LEVEL", level),
                         ("AGENT_BUS_LOG_FILE", file or str(written))):
            monkeypatch.delenv(var, raising=False)
            if val is not None:
                monkeypatch.setenv(var, val)
        log.configure(force=True)
        _at.dest = file or str(written)
        return log.configure(force=False)

    _at.dest = str(written)
    yield _at
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)


def _read(dest):
    out = []
    try:
        with open(dest, encoding="utf-8") as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
    except OSError:
        pass
    return out


def _records(capsys):
    """Kept for the tests that assert stderr specifically."""
    out = []
    for line in capsys.readouterr().err.splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def test_a_record_says_which_build_produced_it(logging_at, capsys):
    """The operator sees a version deployed; the attached session sees another.

    Both are looking at the truth, and without the version on the line there is
    no way to tell which. hatch-vcs appends the commit, so this identifies the
    exact build and not just the release.
    """
    logging_at("INFO")

    @log.logged
    def verb(x=None):
        return "ok"

    verb(x=1)
    rec = _read(logging_at.dest)[-1]
    assert rec["v"], rec
    assert rec["severity"] == "INFO"
    assert rec["pid"] > 0


def test_a_message_body_is_measured_never_copied(logging_at, capsys):
    """A log that copies message text is a second inbox with a different
    lifetime and no TTL -- a worse leak than any diagnosis is worth."""
    logging_at("INFO")

    @log.logged
    def send(to=None, text=None, summary=None):
        return None

    send(to="someone", text="the secret body", summary="s")
    rec = _read(logging_at.dest)[-1]
    assert rec["args"]["text_len"] == len("the secret body")
    assert "the secret body" not in json.dumps(rec)
    assert rec["args"]["to"] == "someone", "addressing is kept; it is not content"


def test_arguments_cannot_overwrite_who_emitted_the_record(logging_at, capsys):
    """`kind` is both an argument and an identity. Merged, a filtered listing
    would rewrite what the caller claims to be.

    The emitter is registered as something the argument is not, so the two are
    told apart by what they say rather than by what the developer's machine
    happens to be running. Asserting only that the record's kind is *not*
    "claude" was satisfiable by an empty record, and unsatisfiable on a laptop
    with a live Claude session -- true for neither of the reasons that matter.

    The bus this registers into is the isolated one every test now gets from
    tests/conftest.py, which is what stops `log._who()` finding a real session.
    """
    from agent_bus import store

    store.register("the-emitter", "omp")
    logging_at("INFO")

    @log.logged
    def list_agents(kind=None):
        return []

    list_agents(kind="claude")
    rec = _read(logging_at.dest)[-1]
    assert rec["args"]["kind"] == "claude", "the argument is recorded"
    assert rec["kind"] == "omp", "and it did not become the emitter's identity"
    assert rec["agent"] == "the-emitter"


def test_unset_is_not_silent(logging_at, capsys):
    """Unset means WARNING: a failure still has to reach someone. Only the
    per-call traffic is opt-in."""
    logging_at(None)

    @log.logged
    def verb():
        return None

    verb()
    logging.getLogger(log.LOGGER_NAME).warning("something went wrong")
    kinds = [r.get("severity") for r in _read(logging_at.dest)]
    assert "INFO" not in kinds, "calls should be quiet by default"
    assert "WARNING" in kinds, "failures must not be"


@pytest.mark.parametrize("word", ["off", "none", "quiet", "silent"])
def test_off_means_off(logging_at, capsys, word):
    logging_at(word)
    logging.getLogger(log.LOGGER_NAME).critical("not even this")
    assert _read(logging_at.dest) == []


def test_an_unknown_level_falls_back_rather_than_failing(logging_at, capsys):
    """A typo in a shell variable must not stop an agent starting."""
    logging_at("VERBOSE-ISH")
    logging.getLogger(log.LOGGER_NAME).warning("still here")
    assert [r["severity"] for r in _read(logging_at.dest)] == ["WARNING"]


def test_a_file_destination_takes_the_records(logging_at, capsys, tmp_path):
    dest = tmp_path / "bus.jsonl"
    logging_at("INFO", file=str(dest))
    logging.getLogger(log.LOGGER_NAME).info("to the file")
    assert "to the file" in dest.read_text()
    assert _records(capsys) == [], "and not to stderr as well"


def test_the_decorator_does_not_change_the_verb(logging_at):
    logging_at("INFO")

    @log.logged
    def verb(a, b=2):
        return a + b

    assert verb(1) == 3
    assert verb(1, b=10) == 11


def test_a_failing_verb_still_raises(logging_at, capsys):
    """Recorded, then let go. A logger that swallows an exception is worse
    than no logger."""
    logging_at("INFO")

    @log.logged
    def verb():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        verb()
    rec = _read(logging_at.dest)[-1]
    assert rec["ok"] is False
    assert "boom" in rec["error"]


def test_a_broken_logger_does_not_break_the_call(logging_at, monkeypatch):
    """The whole point of wrapping every emit."""
    logging_at("INFO")
    monkeypatch.setattr(log, "describe", lambda _a: (_ for _ in ()).throw(RuntimeError("x")))

    @log.logged
    def verb():
        return "still returned"

    assert verb() == "still returned"


def test_nothing_is_written_to_stdout(tmp_path):
    """The MCP server speaks JSON-RPC on stdout. A log line there is a protocol
    error, not noise -- so this drives the real entry point and checks."""
    dest = tmp_path / "agent-bus.jsonl"
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from agent_bus import log; log.configure(force=True);"
        "import logging; logging.getLogger(log.LOGGER_NAME).warning('hello')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        env={"AGENT_BUS_LOG_LEVEL": "INFO", "PATH": "/usr/bin:/bin",
             "AGENT_BUS_LOG_FILE": str(dest)},
    )
    assert proc.stdout == "", proc.stdout
    assert "hello" in dest.read_text()


def test_a_state_directory_that_cannot_be_written_falls_back_to_stderr(tmp_path):
    """A log must never stop a process starting. If the standard place is not
    writable -- a read-only home, a locked-down container -- the records go to
    stderr rather than the agent failing to run."""
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from agent_bus import log; log.configure(force=True);"
        "import logging; logging.getLogger(log.LOGGER_NAME).warning('hello')"
    )
    blocked = tmp_path / "no-entry"
    blocked.write_text("not a directory")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        env={"AGENT_BUS_LOG_LEVEL": "INFO", "PATH": "/usr/bin:/bin",
             "XDG_STATE_HOME": str(blocked)},
    )
    assert proc.stdout == "", proc.stdout
    assert "hello" in proc.stderr


def test_which_surface_wrote_the_line_is_stated_not_inferred(tmp_path):
    """One log file holds both surfaces. A `verb` line from the CLI and one
    from the MCP server are otherwise identical.

    `client` is not the answer. It exists only for MCP, and it names the
    transport by accident: `codex-mcp-client` happens to say so,
    `omp-coding-agent` and `grok-shell-agent-bus` do not. Reading a surface off
    a vendor's product name is a guess that works until someone renames it.

    So the entry point says which one it is, and this drives both for real.
    """
    dest = tmp_path / "both.jsonl"
    home = tmp_path / "bus"
    env = {"AGENT_BUS_HOME": str(home), "AGENT_BUS_LOG_FILE": str(dest),
           "AGENT_BUS_LOG_LEVEL": "INFO", "PATH": os.environ["PATH"],
           "PYTHONPATH": os.path.join(REPO, "src")}

    subprocess.run([sys.executable, "-m", "agent_bus", "list", "--json"],
                   capture_output=True, env=env, cwd=REPO, check=False)
    subprocess.run([sys.executable, "-m", "agent_bus", "mcp"], input=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "some-editor"}}}) + "\n",
        capture_output=True, text=True, env=env, cwd=REPO, check=False)

    surfaces = {}
    for line in dest.read_text().splitlines():
        with contextlib.suppress(ValueError):
            rec = json.loads(line)
            surfaces.setdefault(rec.get("surface"), []).append(rec)

    assert "cli" in surfaces, surfaces.keys()
    assert "mcp" in surfaces, surfaces.keys()
    assert None not in surfaces, "every line must say which surface wrote it"
    assert all("mcp" not in (r.get("client") or "") for r in surfaces["mcp"]), (
        "the client name must not be what makes this work"
    )


# --------------------------------------------------- levels that mean something


def test_a_failed_verb_reaches_you_at_the_default_level(logging_at, capsys):
    """The docstring's promise, which was not kept.

    "unset -- warnings and failures still reach the harness's own log through
    stderr." They did not. `_emit` gated every record on isEnabledFor(INFO) and
    emitted at INFO, and the package contained no call above it, so at the
    default level agent-bus was **completely silent, including on failure**.
    A send that raised produced nothing anywhere.
    """
    logging_at(None)  # unset: WARNING

    @log.logged
    def send(to=None, text=None):
        raise ValueError("no such agent: ghost")

    with pytest.raises(ValueError):
        send(to="ghost", text="hi")

    rec = _read(logging_at.dest)[-1]
    assert rec["severity"] == "WARNING"
    assert rec["ok"] is False
    assert "ghost" in rec["error"]


def test_a_successful_verb_is_still_quiet_at_the_default_level(logging_at, capsys):
    """The other half. Failures reaching you must not turn into every call
    reaching you -- per-call traffic stays opt-in, which is what INFO is for."""
    logging_at(None)

    @log.logged
    def send(to=None, text=None):
        return None

    send(to="someone", text="hi")
    assert _read(logging_at.dest) == []


def test_trace_is_a_level_and_it_is_below_debug(logging_at):
    """Python has no TRACE; 5 is the conventional slot beneath DEBUG."""
    assert log.TRACE == 5
    assert log.TRACE < logging.DEBUG
    assert logging.getLevelName(log.TRACE) == "TRACE"
    logging_at("trace")
    assert logging.getLogger(log.LOGGER_NAME).isEnabledFor(log.TRACE)


def test_trace_is_off_at_every_other_level(logging_at):
    for level in (None, "info", "debug"):
        logging_at(level)
        assert not logging.getLogger(log.LOGGER_NAME).isEnabledFor(log.TRACE), level


def test_info_never_carries_a_body_but_trace_may(logging_at, capsys):
    """The rule that must survive a firehose.

    At INFO a body is measured, never copied -- a log that copies message text
    is a second inbox with a different lifetime and no TTL. TRACE is the
    exception and it is deliberate: it exists to take the wire apart, it is
    never on by accident, and the docs say so in as many words.
    """
    logging_at("INFO")
    log.trace("frame", body="the secret body")
    assert "the secret body" not in capsys.readouterr().err

    logging_at("trace")
    log.trace("frame", body="the secret body")
    rec = _read(logging_at.dest)[-1]
    assert rec["severity"] == "TRACE"
    assert rec["body"] == "the secret body"


# ------------------------------------------------- the id, in the record (#108)


def test_a_send_returns_the_id_it_minted(tmp_path):
    """A sender could not reference the message it had just sent.

    `filebus.send` returns `{transport, id, to}` and `messages.send` returned
    `_sent(name, kind)` -- so the id was in hand and discarded. That is a bug
    with or without logging: nothing could be quoted, followed up, or matched
    against an ack.
    """
    from agent_bus import store
    from agent_bus.commands import messages

    store.register("me", "other", pid=os.getpid())
    them = store.register("them", "other", pid=os.getpid())

    sent = messages.send(to="them", text="hello", summary="s")
    assert sent["id"], f"send returned no id: {sent}"
    assert [m["id"] for m in store.get_inbox(them.name)] == [sent["id"]]


def test_the_id_is_logged_as_a_top_level_trace_id(logging_at, capsys):
    """One identifier, one query expression, two places.

    Not in `args`: it was there only when the bridge passed `message_id=`
    explicitly, so it appeared on inbound deliveries and vanished on everything
    else -- the most confusing possible arrangement. Top level or nowhere.
    """
    from agent_bus import store
    from agent_bus.commands import messages

    store.register("me", "other", pid=os.getpid())
    store.register("them", "other", pid=os.getpid())
    logging_at("INFO")

    sent = messages.send(to="them", text="hello", summary="s")
    rec = _read(logging_at.dest)[-1]
    assert rec["trace_id"] == sent["id"]


def test_a_verb_with_no_message_has_no_trace_id(logging_at, capsys):
    """`list_agents` and `self` must not grow an empty one. An empty trace id
    groups every unrelated record under one meaningless trace -- the mistake
    `cloud/logs.py::trace_field` already refuses to make for the request
    trace."""
    from agent_bus.commands import agents

    logging_at("INFO")
    agents.list_agents()
    rec = _read(logging_at.dest)[-1]
    assert rec["verb"] == "list_agents"
    assert "trace_id" not in rec


def test_a_failed_send_still_carries_no_invented_id(logging_at, capsys):
    """A verb that raised produced no message, so there is no journey to
    correlate. The failure record is still emitted -- at WARNING -- it simply
    has nothing to join on, and says so by omission."""
    from agent_bus.commands import messages

    logging_at(None)
    with pytest.raises(ValueError):
        messages.send(to="nobody-at-all", text="hi")
    rec = _read(logging_at.dest)[-1]
    assert rec["severity"] == "WARNING"
    assert "trace_id" not in rec


# ------------------------------------------------ the firehose has a cap (#104)


def test_a_traced_string_is_capped_and_says_what_it_left_out(logging_at, capsys):
    """A record that shortened its own evidence in silence is worse than one
    that did not shorten it at all.

    A body here can be a million characters, and one `write()` that large can
    be split -- which does not lose a record, it produces a file `jq` dies
    halfway through, only ever while someone is taking the wire apart.
    """
    logging_at("trace")
    log.trace("frame", body="x" * 20000)

    rec = _read(logging_at.dest)[-1]
    assert len(rec["body"]) == log.TRACE_FIELD_CAP
    assert rec["body_len"] == 20000, (
        "the untruncated length must survive, or the record cannot say how "
        "much of the frame it is showing you"
    )


def test_a_short_traced_string_is_untouched_and_unannotated(logging_at, capsys):
    """`<field>_len` IS the truncation marker, so it must not appear otherwise
    -- and nothing is appended to the value: an ellipsis in a copied frame is a
    character that was never on the wire."""
    logging_at("trace")
    log.trace("frame", body="the secret body")

    rec = _read(logging_at.dest)[-1]
    assert rec["body"] == "the secret body"
    assert "body_len" not in rec


def test_the_cap_does_not_touch_what_is_not_a_string(logging_at, capsys):
    """Lengths, pids and flags are already small and are what you read first."""
    logging_at("trace")
    log.trace("frame", bytes=4_000_000, ok=True)

    rec = _read(logging_at.dest)[-1]
    assert rec["bytes"] == 4_000_000
    assert rec["ok"] is True
