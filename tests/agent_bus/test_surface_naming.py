"""Every verb is accounted for on both surfaces, or deliberately on one.

#147: the two surfaces reach the same operations under different names, and
nothing had ever checked. `test_commands_layer.py` compares *answers* -- the
shared command layer guarantees the CLI and the MCP server cannot return
different data -- and says nothing about whether an agent can reach an
operation by name at all. A verb added to one surface and forgotten on the
other is invisible to every parity test we had.

**This is not a name-equality guard, and should not become one.** The names
differ on purpose: the CLI is the short form, the MCP tool is `verb_noun`,
which is what each is idiomatically. An agent carrying `read_message` in its
context finds it when told `read`, and vice versa -- the association survives
the prefix, which is the mechanism working rather than a gap to close.
`self` and `register` are "me"-shaped: no `verb_noun` pair makes sense for
them, so they are the same word on both surfaces.

What this refuses is *silence*. A new tool with no CLI verb, or a new verb
with no tool, fails here until someone writes down which it is -- because the
alternative is what #147 found: five mismatched rows sitting unnoticed since
v0.1.0, and `read` living on the CLI and nowhere on MCP until #154.

**There are two MCP servers, and #204 is what the second one costs.** The cloud
connector surface (`cloud/contract.py`) reaches the same operations for a
desktop peer, and this guard checked only the local one -- so that surface
spelled four of them `read`/`write`/`ack`/`list-agents` and nothing noticed.
It is a deliberate subset, not a second vocabulary: fewer tools, same words.
"""

import importlib.util
import pathlib

from agent_bus.cli import build_parser
from agent_bus.mcp_server import TOOLS


def _cloud_contract():
    """Loaded by path, not imported. `cloud/` is flat modules that are never
    installed, and a bare `import contract` would depend on a sys.path this
    suite has no business setting."""
    root = pathlib.Path(__file__).resolve().parents[2]
    path = root / "cloud" / "contract.py"
    assert path.exists(), (
        f"no {path}. If the cloud surface moved, move this guard with it -- "
        "skipping here is how the second server drifted in the first place."
    )
    spec = importlib.util.spec_from_file_location("_cloud_contract", path)
    assert spec is not None and spec.loader is not None, f"no loader for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

#: One operation, both surfaces. CLI short form -> MCP verb_noun.
PAIRED = {
    "list": "list_agents",
    "inbox": "get_inbox",
    "send": "send_message",
    "ack": "ack_message",
    "status": "set_status",
    "read": "read_message",
    "self": "self",          # "me"-shaped: no verb_noun form to give it
    "register": "register",  # likewise
}

#: CLI-only, each for a reason. Silence here is what #147 called an oversight.
CLI_ONLY = {
    "help": "a terminal affordance; an agent already has the tool descriptions",
    "watch": "a process, not a call -- it blocks and streams until killed",
    "listen": "likewise a process: it binds a socket and stays up",
    "join": "CLI semantics -- register plus listen, for a shell that owns a pid",
    "leave": "CLI semantics -- the counterpart, and the pid is the shell's",
    "unregister": "admin: removes someone else by name, not a self-reference",
    "reap": "housekeeping across the whole bus, not one agent's operation",
    "orphans": "likewise -- recovery over mailboxes nothing points at",
    "grok-status": "reads another harness's own registry, not bus state",
    "hook": "a harness lifecycle entry point, invoked by the harness",
    "mcp": "starts the MCP server; a tool for it would be circular",
}


def _cli_verbs() -> set[str]:
    parser = build_parser()
    assert parser._subparsers is not None, "the CLI declares no subcommands"
    sub = next(a for a in parser._subparsers._group_actions
               if getattr(a, "choices", None))
    assert sub.choices is not None, "the subparser action carries no choices"
    return set(sub.choices)


def test_every_mcp_tool_has_a_cli_verb():
    """A tool an agent cannot reach from a shell is half a surface."""
    assert {t["name"] for t in TOOLS} == set(PAIRED.values()), (
        "an MCP tool is not paired with a CLI verb. Add it to PAIRED with the "
        "verb it corresponds to, or say why it is MCP-only -- there is no "
        "MCP_ONLY list yet because there has never been such a tool."
    )


def test_every_cli_verb_is_paired_or_deliberately_cli_only():
    """The direction #147 actually found broken: `read` shipped on the CLI and
    nowhere on MCP, and stayed that way until #154."""
    verbs = _cli_verbs()
    unaccounted = verbs - set(PAIRED) - set(CLI_ONLY)
    assert not unaccounted, (
        f"CLI verbs nobody has classified: {sorted(unaccounted)}. Either pair "
        "each with its MCP tool in PAIRED, or add it to CLI_ONLY with the "
        "reason it does not belong on the other surface."
    )
    stale = (set(PAIRED) | set(CLI_ONLY)) - verbs
    assert not stale, f"classified but no longer a CLI verb: {sorted(stale)}"


def test_a_verb_is_not_both_paired_and_cli_only():
    """The two lists are a partition. Overlap means one of them is a leftover
    from a verb that moved, and the guard would stop noticing it."""
    assert not (set(PAIRED) & set(CLI_ONLY))


def test_cli_only_verbs_carry_a_reason():
    """A bare list would pass this file and still leave the next reader
    guessing, which is the state #147 was filed about."""
    assert all(len(r) > 20 for r in CLI_ONLY.values())


# ------------------------------------------------------- the second MCP server

#: On the local MCP server but deliberately not offered to a cloud connector.
NOT_ON_CLOUD = {
    "register": "the bridge registers on the peer's behalf; a desktop has no pid",
    "set_status": "presence belongs to the bridge's roster snapshot, not the peer",
    "self": "the token already says who the caller is -- there is nothing to ask",
}


def test_the_cloud_surface_uses_the_same_words_as_the_local_one():
    """#204. Two MCP servers reaching the same operations must spell them the
    same way: an agent that learns `get_inbox` on one must not need `read` on
    the other. The cloud surface may offer fewer tools, never other names."""
    cloud = {t["name"] for t in _cloud_contract().TOOLS}
    local = {t["name"] for t in TOOLS}
    assert cloud <= local, (
        f"cloud tools that exist nowhere else: {sorted(cloud - local)}. Give it "
        "the local MCP server's name for the same operation, or add the tool "
        "there too."
    )


def test_every_local_tool_the_cloud_omits_says_why():
    """The omissions are the interesting half: a subset is a decision, and an
    accidental gap looks exactly like a deliberate one until someone writes the
    reason down."""
    cloud = {t["name"] for t in _cloud_contract().TOOLS}
    missing = {t["name"] for t in TOOLS} - cloud
    assert missing == set(NOT_ON_CLOUD), (
        f"unaccounted for on the cloud surface: {sorted(missing ^ set(NOT_ON_CLOUD))}. "
        "Add it to the connector contract, or to NOT_ON_CLOUD with the reason."
    )
    assert all(len(r) > 20 for r in NOT_ON_CLOUD.values())
