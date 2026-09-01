"""Lifecycle adapters: harnesses that can host an agent-bus session.

Only two, and that is the point. A harness we can merely observe implements
discovery alone; being visible in a listing and being able to register are
different capabilities, and the tree now says so rather than leaving it in a
tuple in another module.
"""

from __future__ import annotations

import os
from typing import Any

from . import claude, grok

# Order is load-bearing: grok MUST come before claude.
#
# This said the predicates "are meant to be mutually exclusive", which invites
# reordering or alphabetising. They are not. `claude.detect` fires on
# CLAUDE_PLUGIN_ROOT *or* CLAUDE_PROJECT_DIR, and grok's hook runner sets
# CLAUDE_PROJECT_DIR on every hook process as a deliberate compat alias -- see
# the quoted Rust at docs/harnesses/grok-build-ipc-reference.md:927,943. So in
# every grok hook both predicates are true and only the order decides.
#
# Reversed, a grok session registers as kind="claude", which suppresses its
# shim listener at lifecycle.py:150 and leaves it unreachable by native send.
ADAPTERS: tuple[Any, ...] = (grok, claude)


def for_kind(kind: str) -> Any | None:
    for adapter in ADAPTERS:
        if kind == adapter.KIND:
            return adapter
    return None


def identify_mcp_client(
    client_info: dict[str, Any] | None,
    env: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Which harness launched us, from the MCP handshake rather than the env.

    A harness that runs our MCP server tells us nothing about itself in the
    environment -- probed 2026-08-24, codex hands its MCP child exactly HOME,
    LANG, LOGNAME, PATH, SHELL, TERM, TMPDIR, USER and __CF_USER_TEXT_ENCODING
    -- so an MCP-only peer registers as `pending-<pid>`: not `other`,
    which would claim we had looked and could not place it, but a plain
    statement that nobody has said anything yet.

    It does say so in `initialize`. Observed clientInfo.name values:

        codex-mcp-client     codex 0.149.0
        omp-coding-agent     omp 1.0.0
        grok-shell-<server>  grok 1.0.5   -- embeds OUR server's name, so the
                                             match is a prefix, not equality

    No claude pattern on purpose: a Claude session running our MCP server is a
    misconfiguration, and `other` is the honest answer to it.

    Returns (kind, session_id). The session id is only ever read *after* the
    client identified itself -- see the note in grok.detect().
    """
    name = str((client_info or {}).get("name") or "").strip().lower()
    if not name:
        return None, None
    e = dict(os.environ if env is None else env)

    if name == "codex-mcp-client":
        # Nothing to link to: codex tells its MCP child no thread id, and its
        # threads are not roster entries. It uses the file bus like any peer.
        return "codex", None
    if name == "omp-coding-agent":
        return "omp", None
    if name.startswith("grok-shell"):
        # GROK_SESSION_ID *is* present here, and reading it is safe precisely
        # because clientInfo already proved grok launched us as its MCP
        # server. grok.detect() still refuses it, for the reason recorded
        # there: through a plain shell that variable is inherited by anything,
        # including a Claude session, which would then adopt a grok identity.
        return grok.KIND, (e.get("GROK_SESSION_ID") or None)
    return None, None


__all__ = ["ADAPTERS", "claude", "for_kind", "grok", "identify_mcp_client"]
