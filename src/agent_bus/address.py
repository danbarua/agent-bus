"""What an agent's id *means*. A leaf: imports nothing from this package.

`RosterEntry.id` was an opaque string, and every policy that should follow from
what kind of address it is was instead hardcoded to a pid -- the discovery gate,
the prune rule, the visibility rule. That is right for a Claude session, whose
identity really is a live process, and wrong for a Codex thread, which is
addressable precisely when nothing is running. An Address carries its own
policy instead.

Canonical spelling is `<kind>:<space>:<value>`. The kind stays in the address
because routing is keyed on kind and only on kind (`transport.for_kind`); an
address without one could not pick a channel without asking every space "is
this yours", which is the expensive last-resort path promoted to the normal
one. The triple is also already the half-formed convention on disk --
`codex:pid:<pid>`, `omp:tty:<pid>`.

Two properties everything else leans on:

**parse() is total.** It never raises, and an unrecognised space parses anyway
and gets the default policy. This is the open-`Kind` decision applied to the
space axis: a harness we have not heard of must be able to name its own
namespace without us having to know about it first.

**An id we parsed is never re-rendered.** `text` is the spelling it arrived in,
`__str__` returns it verbatim, and equality is on `text` so whole-string
comparisons against a plain str keep working. Canonicalising legacy ids would
move their inbox filenames -- and one of the jobs of this change is to recover
inboxes that were orphaned exactly that way.
"""

from __future__ import annotations

from dataclasses import dataclass

BUS = "bus"
SESSION = "session"
PID = "pid"
THREAD = "thread"

KNOWN_SPACES: tuple[str, ...] = (BUS, SESSION, PID, THREAD)

# `omp:tty:<pid>` is a pid address that says how the pid was found. The space
# it belongs to is what matters, not the route we took to it.
SPACE_ALIASES: dict[str, str] = {"tty": PID}


@dataclass(frozen=True, eq=False)
class Address:
    """A parsed agent id. Compare it to a plain str and it still works."""

    kind: str | None
    space: str
    value: str
    text: str

    def __str__(self) -> str:
        return self.text

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Address):
            return self.text == other.text
        if isinstance(other, str):
            return self.text == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.text)


def parse(text: str, kind_hint: str | None = None) -> Address:
    """Read an id. Never raises; an unknown shape gets the default policy."""
    raw = str(text)
    parts = raw.split(":", 2)

    if len(parts) == 1:
        # A bare uuid: what register() has always minted for its own roster.
        return Address(kind=kind_hint, space=BUS, value=raw, text=raw)

    if len(parts) == 2:
        # The legacy two-part discovered form -- `claude:<sessionId>`,
        # `grok:<sessionId>`, `omp:<id>`. The middle term was never written, so
        # the space is implied by what these have always been: harness sessions.
        return Address(kind=parts[0] or kind_hint, space=SESSION, value=parts[1], text=raw)

    kind, space, value = parts
    return Address(
        kind=kind or kind_hint,
        space=SPACE_ALIASES.get(space, space),
        value=value,
        text=raw,
    )


def mint(kind: str | None, space: str, value: str) -> Address:
    """Build a *new* address canonically. Only new ones; parsed ones keep theirs."""
    text = f"{kind or ''}:{space}:{value}"
    return Address(kind=kind, space=space, value=value, text=text)


__all__ = [
    "BUS",
    "KNOWN_SPACES",
    "PID",
    "SESSION",
    "SPACE_ALIASES",
    "THREAD",
    "Address",
    "mint",
    "parse",
]
