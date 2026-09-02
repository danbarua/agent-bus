"""The preflight's verdict, against surfaces it is handed rather than a repo.

`_breaking` is the half with judgement in it, and the half that goes wrong
quietly: a check that stopped detecting something would report "no breaking
surface change" in exactly the voice it uses when there genuinely is none.

Driven with literal surface dicts, not by tagging anything. The extraction half
-- running a probe inside a `git archive` of an old tag -- is exercised every
time the script is run for real, and needs a repo with history to mean
anything.
"""

from __future__ import annotations

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))


def _preflight():
    """Loaded by path: `scripts/` is not a package and is never installed."""
    path = os.path.join(REPO, "scripts", "release_preflight.py")
    spec = importlib.util.spec_from_file_location("_preflight", path)
    assert spec and spec.loader, f"no loader for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _surface(**over):
    base = {"cli": {}, "bridge_cli": {}, "mcp_tools": {}, "cloud_tools": {},
            "plist_argv": [], "entry_points": {}, "errors": []}
    base.update(over)
    return base


def _tool(props, required=()):
    return {"properties": sorted(props), "required": sorted(required)}


def test_a_removed_cli_flag_is_breaking():
    """#227's shape: a flag rename is a removal to anything already typing it."""
    p = _preflight()
    old = _surface(cli={"inbox": ["--json", "--name"]})
    new = _surface(cli={"inbox": ["--json", "--target"]})
    found = p._breaking(old, new, "pypi")
    assert any("`--name` removed" in f for f in found), found


def test_a_removed_mcp_field_is_breaking():
    """#224. A connector pins the schema at connect time, so a removed field
    reaches sessions that will never re-read it."""
    p = _preflight()
    old = _surface(mcp_tools={"get_inbox": _tool(["name", "unread_only"])})
    new = _surface(mcp_tools={"get_inbox": _tool(["unread_only"])})
    found = p._breaking(old, new, "pypi")
    assert found == ["MCP get_inbox: field `name` removed"], found


def test_a_newly_required_field_is_breaking():
    """Adding a field is safe; requiring one that was optional is not -- every
    caller that omitted it now fails."""
    p = _preflight()
    old = _surface(mcp_tools={"send_message": _tool(["to", "text"], ["to"])})
    new = _surface(mcp_tools={"send_message": _tool(["to", "text"], ["to", "text"])})
    found = p._breaking(old, new, "pypi")
    assert any("`text` is now required" in f for f in found), found


def test_gaining_a_verb_requirement_is_breaking_even_though_nothing_was_removed():
    """#218. The bridge had no subcommands and now demands one, so an installed
    plist's argv stops parsing -- and that shows up as neither a removed verb
    nor a removed flag. Without this case the preflight would have called
    v0.4.0 a patch."""
    p = _preflight()
    old = _surface(bridge_cli={})
    new = _surface(bridge_cli={"start": ["--kind", "--name"]})
    found = p._breaking(old, new, "pypi")
    assert any("now requires a verb" in f for f in found), found


def test_a_changed_service_invocation_is_breaking():
    p = _preflight()
    old = _surface(plist_argv=["__BIN__/agent-bridge", "--kind", "__KIND__"])
    new = _surface(plist_argv=["__BIN__/agent-bridge", "start", "--kind", "__KIND__"])
    found = p._breaking(old, new, "pypi")
    assert any("service invocation changed" in f for f in found), found


def test_additions_are_not_breaking():
    """The other half of the verdict. A preflight that called every change
    breaking would be ignored within a week."""
    p = _preflight()
    old = _surface(cli={"inbox": ["--json"]}, mcp_tools={"get_inbox": _tool(["a"])})
    new = _surface(
        cli={"inbox": ["--json", "--new"], "read": ["--json"]},
        mcp_tools={"get_inbox": _tool(["a", "b"]), "read_message": _tool(["id"])},
    )
    assert p._breaking(old, new, "pypi") == []


def test_a_cloud_tag_is_not_judged_on_the_packages_surface():
    """The two namespaces move independently. A `cloud-v*` tag calling itself
    breaking because the *package's* MCP schema changed would be reporting
    someone else's news -- and a verdict that cries wolf stops being read."""
    p = _preflight()
    old = _surface(mcp_tools={"get_inbox": _tool(["name"])},
                   plist_argv=["a", "--kind"],
                   cloud_tools={"read": _tool(["unread_only"])})
    new = _surface(mcp_tools={"get_inbox": _tool([])},
                   plist_argv=["a", "start", "--kind"],
                   cloud_tools={"read": _tool(["unread_only"])})

    assert p._breaking(old, new, "cloud") == []
    assert p._breaking(old, new, "pypi"), "the same change must still count for pypi"


def test_a_removed_connector_tool_is_breaking_for_a_cloud_tag():
    """#204's surface. ChatGPT caches discovery, so a renamed tool reaches
    sessions that never re-read it."""
    p = _preflight()
    old = _surface(cloud_tools={"read": _tool([]), "write": _tool([])})
    new = _surface(cloud_tools={"get_inbox": _tool([]), "send_message": _tool([])})
    found = p._breaking(old, new, "cloud")
    assert sorted(found) == ["connector: tool `read` removed",
                             "connector: tool `write` removed"], found


def test_the_namespace_comes_from_the_tag_prefix():
    p = _preflight()
    assert p._namespace("v0.4.0") == "pypi"
    assert p._namespace("cloud-v0.0.4") == "cloud"
