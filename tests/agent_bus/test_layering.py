"""The store is not the public surface. This is what says so.

agent-bus has a layer boundary that nothing in the file tree makes visible:
`store.py` sits alongside `cli.py` and `mcp_server.py`, so nothing signals that
one is beneath the others. The rule lived only in prose -- and prose lost. The
bridge was written against `store.register` / `store.get_inbox` /
`store.ack_message` within hours of a plan that said, in as many words, to use
the router instead.

What that cost, concretely: `store.register` claims a name and stops.
`lifecycle.session_start` claims a name *and publishes a listener*, which is
what puts a peer in Claude's native ListAgents and gives it a socket to reply
from. Reaching one layer down did not look like a shortcut. It looked like
registering, and it silently produced a peer Claude could not message.

So the guard is an allowlist, not a ban. Every entry below is a module that has
earned direct access, with the reason. **Adding yourself to it is the point** --
it is a deliberate edit a reviewer sees, rather than an import nobody notices.

If this test fails for your module, the question to answer first is whether
`commands.agents` or `commands.messages` already does what you want. They
usually do, and they do the extra things you did not know were needed.
"""

from __future__ import annotations

import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")

# module path -> why it may talk to the store directly.
ALLOWED: dict[str, str] = {
    "agent_bus/commands/agents.py": "IS the public verb layer for identity",
    "agent_bus/commands/messages.py": "IS the public verb layer for mail",
    "agent_bus/lifecycle.py": "a peer of commands/, not a consumer: session start and end",
    "agent_bus/adapters/transport/filebus.py": (
        "the file bus writes messages; the documented odd edge in the "
        "store -> adapters -> transport -> store cycle"
    ),
    "agent_bus/uds.py": "the wire itself, below the verb layer",
    "agent_bus/watch.py": "owns the inbox file offset and its compaction",
    "agent_bus/listener.py": "needs get_home to place listener pid files",
    "agent_bus/mcp_server.py": "needs get_self to answer the identity handshake",
    "agent_bus/cli.py": "admin verbs with no commands equivalent: reap, adopt-orphan, unregister",
}


def _modules() -> list[tuple[str, ast.Module]]:
    out = []
    for root, _, files in os.walk(SRC):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            if os.path.join(root, fn) == os.path.join(SRC, "agent_bus", "store.py"):
                continue  # it is the store; it is not a consumer of itself
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, SRC)
            with open(path, encoding="utf-8") as f:
                out.append((rel, ast.parse(f.read(), filename=rel)))
    return out


def _touches_store(tree: ast.Module) -> bool:
    """Both spellings, at module scope or inside a function.

    `from .. import store` and `from .store import get_home` look nothing alike
    to a grep and are the same thing to the layering.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[-1] == "store":
                return True
            if any(a.name == "store" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "store" for a in node.names):
                return True
    return False


def test_only_allowlisted_modules_reach_into_the_store():
    offenders = [
        rel for rel, tree in _modules()
        if _touches_store(tree) and rel not in ALLOWED
    ]
    assert not offenders, (
        "these modules import `store` directly:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe store is the mechanical writer, not the public surface. Use\n"
        "`commands.agents` / `commands.messages`, or `lifecycle.session_start`\n"
        "if you need to join the bus -- session_start also publishes a listener,\n"
        "which is what makes a peer reachable by Claude's native SendMessage.\n"
        "store.register alone silently does not.\n\n"
        "If direct access is genuinely right, add the module to ALLOWED in this\n"
        "file with the reason. That edit is the review, and is deliberate."
    )


def test_the_allowlist_has_no_stale_entries():
    """An allowlist that outlives its reasons stops being read.

    A module that no longer touches the store should not keep permission to,
    or the list drifts into decoration and the next reviewer trusts it less.
    """
    touching = {rel for rel, tree in _modules() if _touches_store(tree)}
    stale = sorted(set(ALLOWED) - touching)
    assert not stale, (
        f"these are allowlisted but no longer import the store: {stale}. "
        "Remove them, so the list keeps meaning something."
    )


def test_the_bridge_is_not_allowlisted():
    """The case that prompted all of this, pinned.

    The bridge is an ordinary peer and must stay on the public surface -- that
    is the whole claim it makes about itself. If it ever appears in ALLOWED,
    that claim has quietly stopped being true.
    """
    assert "agent_bridge/bridge.py" not in ALLOWED
