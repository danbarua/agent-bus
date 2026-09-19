"""Legacy `log.info/warn/trace(message, **kwargs)` calls only ever go down.

Those take any key a caller invents and never require a message id, which is
what the typed events exist to prevent. `agent_bridge` has none left. The rest
is counted per file: converting a file lowers its number here, and a number
that goes up is a call somebody added on purpose, in a diff a reviewer sees.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
LEGACY_LOGGERS = frozenset({"log", "bus_log"})
LEGACY_LEVELS = frozenset({"info", "warn", "trace"})

# path under src/ -> how many legacy calls it still makes.
LEGACY_CALLS = {
    "agent_bus/mcp_server.py": 34,
    "agent_bus/uds.py": 25,
    "agent_bus/commands/agents.py": 4,
    "agent_bus/watch.py": 1,
    "agent_bus/log.py": 1,
}


def _legacy_calls(path: pathlib.Path) -> int:
    tree = ast.parse(path.read_text())
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LEGACY_LEVELS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in LEGACY_LOGGERS
    )


def _counts() -> dict[str, int]:
    found = {str(p.relative_to(SRC)): _legacy_calls(p) for p in sorted(SRC.rglob("*.py"))}
    return {path: n for path, n in found.items() if n}


def test_agent_bridge_makes_no_legacy_log_calls():
    bridge = {p: n for p, n in _counts().items() if p.startswith("agent_bridge/")}
    assert bridge == {}, f"log through agent_bridge/events.py instead: {bridge}"


def test_the_legacy_calls_in_agent_bus_are_exactly_the_allowlist():
    in_bus = {p: n for p, n in _counts().items() if not p.startswith("agent_bridge/")}
    assert in_bus == LEGACY_CALLS, (
        "a file's legacy call count changed: if it went down, lower it in "
        "LEGACY_CALLS; if it went up, use a typed event instead"
    )


def test_the_scan_sees_a_call_it_should(tmp_path):
    src = tmp_path / "x.py"
    src.write_text("log.info('a', k=1)\nbus_log.warn('b')\nlog.trace('c')\nother.info('d')\n"
                   "log.emit(e)\nlog.configure()\n")
    assert _legacy_calls(src) == 3
