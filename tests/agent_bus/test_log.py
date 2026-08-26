"""What the logger must never do, and the one thing it must always say.

Logging is the code most likely to be wrong in a way nobody notices: it runs
everywhere, its output is read only during an incident, and a mistake in it
looks like a mistake in whatever it was describing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import sys

import pytest

from agent_bus import log


@pytest.fixture
def logging_at(monkeypatch, capsys):
    """Configure the logger at a level and hand back whatever it emitted."""

    def _at(level=None, file=None):
        for var, val in (("AGENT_BUS_LOG_LEVEL", level), ("AGENT_BUS_LOG_FILE", file)):
            monkeypatch.delenv(var, raising=False)
            if val is not None:
                monkeypatch.setenv(var, val)
        log.configure(force=True)
        return log.configure(force=False)

    yield _at
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)


def _records(capsys):
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
    rec = _records(capsys)[-1]
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
    rec = _records(capsys)[-1]
    assert rec["args"]["text_len"] == len("the secret body")
    assert "the secret body" not in json.dumps(rec)
    assert rec["args"]["to"] == "someone", "addressing is kept; it is not content"


def test_arguments_cannot_overwrite_who_emitted_the_record(logging_at, capsys):
    """`kind` is both an argument and an identity. Merged, a filtered listing
    would rewrite what the caller claims to be."""
    logging_at("INFO")

    @log.logged
    def list_agents(kind=None):
        return []

    list_agents(kind="claude")
    rec = _records(capsys)[-1]
    assert rec["args"]["kind"] == "claude"
    assert rec.get("kind") != "claude" or "agent" not in rec


def test_unset_is_not_silent(logging_at, capsys):
    """Unset means WARNING: a failure still has to reach someone. Only the
    per-call traffic is opt-in."""
    logging_at(None)

    @log.logged
    def verb():
        return None

    verb()
    logging.getLogger(log.LOGGER_NAME).warning("something went wrong")
    kinds = [r.get("severity") for r in _records(capsys)]
    assert "INFO" not in kinds, "calls should be quiet by default"
    assert "WARNING" in kinds, "failures must not be"


@pytest.mark.parametrize("word", ["off", "none", "quiet", "silent"])
def test_off_means_off(logging_at, capsys, word):
    logging_at(word)
    logging.getLogger(log.LOGGER_NAME).critical("not even this")
    assert _records(capsys) == []


def test_an_unknown_level_falls_back_rather_than_failing(logging_at, capsys):
    """A typo in a shell variable must not stop an agent starting."""
    logging_at("VERBOSE-ISH")
    logging.getLogger(log.LOGGER_NAME).warning("still here")
    assert [r["severity"] for r in _records(capsys)] == ["WARNING"]


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
    rec = _records(capsys)[-1]
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


def test_nothing_is_written_to_stdout():
    """The MCP server speaks JSON-RPC on stdout. A log line there is a protocol
    error, not noise -- so this drives the real entry point and checks."""
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from agent_bus import log; log.configure(force=True);"
        "import logging; logging.getLogger(log.LOGGER_NAME).warning('hello')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        env={"AGENT_BUS_LOG_LEVEL": "INFO", "PATH": "/usr/bin:/bin"},
    )
    assert proc.stdout == "", proc.stdout
    assert "hello" in proc.stderr
