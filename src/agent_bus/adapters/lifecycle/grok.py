"""Grok lifecycle: what core asks to place a grok session on the bus."""
from __future__ import annotations

import glob
import json
import os
from typing import Any
from urllib.parse import quote

from ...paths import grok_dir as _grok_dir

KIND = "grok"


def _session_title(gdir: str, session_id: str, cwd: str | None) -> str | None:
    """Display title from summary.json (dashboard rename), not agent_name (persona).

    Lived in the discovery adapter until #184 retired it, and moved here rather
    than being deleted with it: this reads a real directory that really exists
    (`~/.grok/sessions/<pct-encoded cwd>/<session_id>/summary.json`, verified
    against live files) and is the one part of that module that ever worked.
    Its only caller was always `session_name` below.
    """
    paths: list[str] = []
    if cwd:
        paths.append(
            os.path.join(gdir, "sessions", quote(cwd, safe=""), session_id, "summary.json")
        )
    paths.extend(
        glob.glob(os.path.join(gdir, "sessions", "*", session_id, "summary.json"))
    )
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            title = data.get("generated_title")
            if isinstance(title, str) and title.strip():
                return title.strip()
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None


def detect(env: dict[str, str]) -> bool:
    """Hook-scoped signals only.

    Deliberately not GROK_SESSION_ID. That is set on the Bash/PTY tool's
    environment, so a shell spawned by Grok carries it and anything launched
    from that shell inherits it -- including a Claude session, which would then
    adopt a Grok identity and, on exit, unregister the live Grok one.

    There is now one place that *does* read GROK_SESSION_ID:
    `adapters.lifecycle.identify_mcp_client`. That is not a reversal of this
    rule, it is the case the rule was guarding against being unable to tell
    apart. Grok passes the variable to its MCP children too (verified), but
    there the MCP `initialize` handshake has already named the client
    `grok-shell-<server>`, which only grok's own MCP client sends -- a Claude
    or omp session sitting inside a grok shell announces itself, not grok. The
    variable is read only after that match, never before, and never here.
    """
    return bool(env.get("GROK_HOOK_EVENT") or env.get("GROK_PLUGIN_ROOT"))


def session_id(payload: dict[str, Any] | None, env: dict[str, str]) -> str | None:
    if payload:
        sid = payload.get("sessionId") or payload.get("session_id")
        if sid:
            return str(sid)
    return env.get("GROK_SESSION_ID") or None


def host_pid(session_id: str | None, env: dict[str, str]) -> int | None:
    """The pid of the grok session, not of the hook process running this."""
    if not session_id:
        return None
    # Correct if the file is populated, and in practice it never is: grok
    # prunes active_sessions.json to `[]` at startup and does not repopulate
    # it, which is why #184 retired the discovery adapter that read the same
    # file. Kept rather than stubbed to None because the read is cheap and
    # right if grok's behaviour changes -- but assume this returns None, and
    # that every grok session therefore takes lifecycle.host_pid()'s getppid()
    # fallback. That is what makes grok the one MCP-capable harness whose CLI
    # users must pass --pid explicitly.
    path = os.path.join(_grok_dir(), "active_sessions.json")
    try:
        with open(path, encoding="utf-8") as f:
            sessions = json.load(f)
        if isinstance(sessions, list):
            for s in sessions:
                if str(s.get("session_id") or "") == session_id:
                    pid = s.get("pid")
                    if pid:
                        return int(pid)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def session_name(session_id: str | None, cwd: str | None) -> str | None:
    """Grok titles its sessions; prefer that over a derived name."""
    if not session_id:
        return None
    return _session_title(_grok_dir(), session_id, cwd)


def workspace(env: dict[str, str]) -> str | None:
    return env.get("GROK_WORKSPACE_ROOT")
