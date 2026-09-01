"""`session_name` reads grok's real per-session summary, which does exist.

The rest of grok's `~/.grok` surface does not record a live session -- #184
retired the discovery adapter over it -- but this one directory is real and
verified against live files: `~/.grok/sessions/<pct-encoded cwd>/<session_id>/
summary.json` carries a `generated_title`. The helper that reads it moved here
with the adapter's deletion, because `session_name` was always its only caller.

It prefers `generated_title` (what the dashboard shows after a rename) over
`agent_name` (the persona), which is the distinction the field names hide.
"""

import json
import os
from urllib.parse import quote

from agent_bus.adapters.lifecycle import grok


def test_session_name_prefers_the_dashboard_title(tmp_path, monkeypatch):
    gdir = str(tmp_path / "grok")
    cwd = "/Users/dan/Code/agents/exo-ledger"
    sid = "01a02536-fd0c-7781-8ca0-f9ed67563714"
    summary_dir = os.path.join(gdir, "sessions", quote(cwd, safe=""), sid)
    os.makedirs(summary_dir)
    with open(os.path.join(summary_dir, "summary.json"), "w") as f:
        json.dump({"generated_title": "exo-grok", "agent_name": "grok-build-plan"}, f)

    monkeypatch.setattr(grok, "_grok_dir", lambda: gdir)
    assert grok.session_name(sid, cwd) == "exo-grok"


def test_session_name_is_none_when_there_is_no_summary(tmp_path, monkeypatch):
    """Best-effort: a session grok has not titled is unnamed, not an error."""
    monkeypatch.setattr(grok, "_grok_dir", lambda: str(tmp_path / "grok"))
    assert grok.session_name("no-such-session", "/tmp") is None
