"""A tool description says how to use the tool. Nothing else.

This keeps regressing, so it gets a check rather than another correction.

An MCP tool description is read by an agent deciding whether and how to call
something. It should answer: what does this do, what do I pass, what comes
back, and what must I not do with the result. That is the whole brief.

What kept leaking in instead:

    "List live agent-bus roster (file bus u native Claude/Grok/omp/Codex)."
    "agent-bus picks the channel that agent's harness actually reads -- a live
     hand-off to a Claude peer, a durable queue for Codex, the file bus
     otherwise -- and the reply names the transport used."
    "Show this process's file-bus registration (walks ancestor pids)."

Those are architecture notes and design-decision records. They are worth having
-- in the module, in the design doc, in the commit that made the decision --
and they are worth nothing to a caller. Worse than nothing: the entire promise
of this bus is that you name an agent and do not think about how it is reached.
A description that explains the transports breaks that promise in the one place
the caller is definitely reading.

The same applies to rationale and to rejected alternatives. "An unknown target
is an error, **not an empty inbox**" tells a caller about a design we did not
choose. "Fails **rather than being silently filed**" argues with an alternative
nobody asked about. Both shrink to the fact: it errors, it fails.

Parameter descriptions are exempt from the vocabulary rule: telling a caller
which values `kind` accepts means naming the harnesses, and that is usage.
"""

from __future__ import annotations

import ast
import os
import re

from agent_bus.mcp_server import TOOLS

CLI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src", "agent_bus", "cli.py",
)

# Words that describe how the bus is built. A caller reaching one of these in a
# description has been told something they cannot act on.
ARCHITECTURE = [
    "file bus", "file-bus", "filebus",
    "uds", "socket", "pid",
    "transport", "channel",
    "roster", "jsonl", "ancestor",
]

# Naming a specific harness in a *description* means the caller is being told
# how delivery differs per harness -- which is exactly what they should not
# need to know. (Parameter descriptions may name them; see the module docstring.)
HARNESSES = ["claude", "codex", "grok", "omp"]

# Long descriptions are where rationale hides. The longest legitimate one here
# is get_inbox, which has a real safety instruction to carry.
MAX_DESCRIPTION = 300


def _words(text: str) -> str:
    return re.sub(r"[^a-z ]+", " ", text.lower())


def test_no_tool_description_explains_the_architecture():
    bad = []
    for tool in TOOLS:
        haystack = _words(tool["description"])
        for term in ARCHITECTURE:
            if re.search(rf"\b{re.escape(term)}\b", haystack):
                bad.append(f"{tool['name']}: mentions {term!r}")
    assert not bad, (
        "tool descriptions describe how to USE the tool, not how it is built:\n  "
        + "\n  ".join(bad)
        + "\n\nThe promise of this bus is that a caller names an agent and does "
        "not think about how it is reached. Explaining the mechanism in the one "
        "place they are certainly reading breaks that promise. Put it in the "
        "module docstring or the design doc instead."
    )


def test_no_tool_description_names_a_harness():
    bad = []
    for tool in TOOLS:
        haystack = _words(tool["description"])
        for name in HARNESSES:
            if re.search(rf"\b{name}\b", haystack):
                bad.append(f"{tool['name']}: names {name!r}")
    assert not bad, (
        "tool descriptions must not name harnesses:\n  " + "\n  ".join(bad)
        + "\n\nA caller does not send to a Claude peer or a Codex thread. They "
        "send to an agent. Naming harnesses here tells them delivery differs "
        "per harness -- the one thing the bus exists to hide.\n"
        "Parameter descriptions may name harnesses: which values `kind` takes "
        "is usage."
    )


def test_descriptions_stay_short_enough_to_be_instructions():
    """Rationale arrives as length. A description that needs a paragraph has
    usually stopped saying what to do and started explaining why."""
    long = [
        f"{t['name']}: {len(t['description'])} chars"
        for t in TOOLS
        if len(t["description"]) > MAX_DESCRIPTION
    ]
    assert not long, (
        f"over {MAX_DESCRIPTION} characters:\n  " + "\n  ".join(long)
        + "\n\nCheck what the extra length is doing. If it is explaining a "
        "decision or arguing with an alternative, it belongs in the code."
    )


def test_every_tool_actually_has_one():
    missing = [t["name"] for t in TOOLS if not t.get("description", "").strip()]
    assert not missing, f"no description: {missing}"


def test_the_check_is_looking_at_the_real_surface():
    """A guard that inspected an empty list would pass forever."""
    assert len(TOOLS) >= 5
    assert {"send_message", "get_inbox", "list_agents"} <= {t["name"] for t in TOOLS}


# ------------------------------------------------------- the CLI text output


def test_the_cli_text_output_does_not_name_a_transport():
    """`agent-bus send` used to print "sent via claude-uds to labkit-dev".

    The CLI grew as a debugging entrypoint -- key=value pairs, internal ids,
    the channel that carried it -- and that shape stayed after people started
    using it for real. The text form is for a reader; `--json` is where a
    caller that genuinely wants the mechanism should look, and it still carries
    everything.

    Checked at the source rather than by running commands, so it covers the
    paths a test would have to construct a whole bus to reach.
    """
    tree = ast.parse(open(CLI, encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print"):
            continue
        # stderr is diagnostics for an operator, not output for a reader.
        if any(kw.arg == "file" for kw in node.keywords):
            continue
        text = ast.unparse(node).lower()
        for term in ("transport", "socket", "uds"):
            if term in text:
                bad.append(f"cli.py:{node.lineno}: prints {term!r}")
    assert not bad, (
        "the CLI text output names the mechanism:\n  " + "\n  ".join(bad)
        + "\n\nSay what happened, not how. `--json` carries the rest."
    )


# ------------------------------------------------------ the response shapes


# Keys that describe how the bus works, not who you are talking to. Two of
# these are worse than jargon: `inbox` is a path to a file on disk, and
# `native` carries another process's socket path. `formerNames` (#148) is a
# resolution detail, not something a caller needs in order to address an
# agent -- the name or an alias already does that.
INTERNAL_KEYS = {"inbox", "native", "procStart", "transport", "socket", "formerNames"}


def _public_roster_keys() -> set[str]:
    from agent_bus.protocol import RosterEntry, roster_to_public

    entry = RosterEntry(
        id="x", name="n", kind="omp", pid=1, cwd="/tmp", status="idle",
        inbox="file:/tmp/inboxes/x.jsonl",
        native={"messagingSocketPath": "/tmp/cc-socks/1.sock"},
        registeredAt="t", updatedAt="t", procStart="s", aliases=["omp:session:1"],
    )
    return set(roster_to_public(entry))


def test_the_public_roster_shape_carries_no_internals():
    """`list_agents` returned roster_to_dict, which is the *storage* shape --
    store.py writes it and dict_to_roster reads it back. Handing it to a caller
    meant handing over a path to somebody else's mailbox."""
    leaked = sorted(_public_roster_keys() & INTERNAL_KEYS)
    assert not leaked, (
        f"roster_to_public exposes {leaked}. That is the storage shape leaking "
        "into an answer. roster_to_dict stays for disk; this one is what an "
        "agent is told."
    )


def test_the_public_roster_shape_is_still_addressable():
    """Stripping is only safe while what remains is enough to write back with.
    An id and a name address an agent; aliases do too, and are how a registered
    row and a discovered one turn out to be the same agent."""
    keys = _public_roster_keys()
    assert {"id", "name", "aliases"} <= keys, (
        f"a caller cannot address anyone with {sorted(keys)}"
    )


def test_the_send_reply_says_what_happened_not_how():
    from agent_bus.commands.messages import _sent

    reply = _sent("labkit-dev", "omp")
    assert not set(reply) & INTERNAL_KEYS, (
        f"the send reply exposes {sorted(set(reply) & INTERNAL_KEYS)}"
    )
    assert reply == {"to": "labkit-dev", "delivery": "now"}
    assert _sent("them", "desktop")["delivery"] == "queued", (
        "the one fact a sender needs beyond 'it went': can I wait for an answer"
    )
