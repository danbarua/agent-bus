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
"""

from agent_bus.cli import build_parser
from agent_bus.mcp_server import TOOLS

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
    sub = next(a for a in build_parser()._subparsers._group_actions
               if getattr(a, "choices", None))
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
