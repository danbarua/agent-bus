"""Core asks adapters; it does not sniff for harnesses itself.

plugin_host.py used to be the mixing bowl: detect_kind() sniffed env vars,
host_pid() was one branch per vendor, and session_start() reached into
adapters.grok for a session title. Anything vendor-specific that was not
discovery had nowhere to go, so it collected there.
"""

import subprocess

from agent_bus.adapters.lifecycle import claude as claude_adapter
from agent_bus.adapters.lifecycle import grok as grok_adapter
from agent_bus.lifecycle import (
    SessionDescriptor,
    describe,
    detect_kind,
    session_start,
)
from agent_bus.protocol import FALLBACK_KIND, AgentTarget
from agent_bus.store import find_entry


def test_each_adapter_answers_the_three_questions():
    """The interface core depends on. If an adapter stops answering one of
    these, core has to start guessing again."""
    for adapter in (grok_adapter, claude_adapter):
        assert isinstance(adapter.KIND, str) and adapter.KIND
        for fn in ("detect", "session_id", "host_pid", "session_name", "workspace"):
            assert callable(getattr(adapter, fn)), f"{adapter.KIND} lacks {fn}"


def test_grok_detects_on_hook_scoped_vars_only():
    """Not GROK_SESSION_ID: it is set on the Bash/PTY tool's environment, so a
    shell spawned by Grok carries it and anything launched from that shell
    inherits it -- including a Claude session, which would then adopt a Grok
    identity and unregister the live Grok one on exit."""
    assert grok_adapter.detect({"GROK_HOOK_EVENT": "SessionStart"}) is True
    assert grok_adapter.detect({"GROK_PLUGIN_ROOT": "/p"}) is True
    assert grok_adapter.detect({"GROK_SESSION_ID": "abc"}) is False


def test_claude_detects_on_its_own_vars():
    assert claude_adapter.detect({"CLAUDE_PLUGIN_ROOT": "/p"}) is True
    assert claude_adapter.detect({"GROK_HOOK_EVENT": "x"}) is False


def test_unknown_harness_still_gets_a_usable_identity():
    """No adapter claims it, and that is not an error: it must still be able to
    register and be addressed, or we have rebuilt the closed enum."""
    desc = describe(payload=None, env={"SOME_OTHER_AGENT": "1"})
    assert desc.kind == FALLBACK_KIND
    assert desc.pid, "must fall back to a pid rather than giving up"
    assert desc.name.startswith(FALLBACK_KIND)
    assert desc.cwd


def test_detect_kind_returns_the_fallback_not_none():
    assert detect_kind({"NOTHING": "1"}) == FALLBACK_KIND


def test_an_explicit_descriptor_bypasses_all_sniffing(tmp_path):
    """The point of the extraction: a caller can state the identity outright
    and core will not consult the environment at all."""
    holder = subprocess.Popen(["sleep", "30"])
    try:
        desc = SessionDescriptor(
            kind="aider",
            session_id="sid-1",
            pid=holder.pid,
            cwd=str(tmp_path),
            name="stated-outright",
        )
        entry = session_start(descriptor=desc, home=str(tmp_path))
        assert entry.name == "stated-outright"
        assert entry.kind == "aider"
        assert find_entry(AgentTarget("stated-outright"), home=str(tmp_path)) is not None
    finally:
        holder.kill()
        holder.wait()


def test_grok_session_name_prefers_the_harness_title(monkeypatch, tmp_path):
    """Grok titles its sessions; a derived kind-<id> name would throw that away."""
    monkeypatch.setenv("AGENT_BUS_GROK_DIR", str(tmp_path))
    assert grok_adapter.session_name(None, None) is None


def test_claude_host_pid_ignores_a_dead_session_file(monkeypatch, tmp_path):
    """A stale <oldpid>.json after a crash must not win: registering a dead pid
    gets pruned on the next roster read, leaving the session invisible."""
    import json

    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(tmp_path))
    dead = subprocess.Popen(["sleep", "30"])
    dead.kill()
    dead.wait()
    (tmp_path / f"{dead.pid}.json").write_text(
        json.dumps({"pid": dead.pid, "sessionId": "sid-x"})
    )
    assert claude_adapter.host_pid("sid-x", {}) is None
