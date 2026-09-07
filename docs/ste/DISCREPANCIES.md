# Discrepancies

Claims kept as written in the mirror because the source states them, but
which look internally inconsistent or under-specified. A separate pass
resolves these against `src/`.

## `docs/running-the-bridge.md`, "Install the service"

The source uses "alias" for a different concept than the glossary term. It
states an alias is a role with exactly one holder, describing one address
mapping to exactly one service. The glossary's `alias` term describes a
second address recorded for one roster entry, so two addresses reconcile
into one row. These are two different meanings for one word.

## `docs/UDS-protocol.md`, section 7 (Safety)

The source states TRACE logging copies frame content by design and "emits
at severity: DEBUG". A TRACE-level log record emitting at `DEBUG` severity,
rather than a `TRACE` severity, looks internally odd.

## `docs/harness-compatibility.md`, "Sending into Codex"

The source states "that makes Codex the easiest of the three to message"
with no antecedent for "the three" stated nearby. The likely referents are
Codex, Grok, and omp, but the source does not say so.

## `docs/identity-and-peering.md`, "How a peer gets an identity"

Two passages describe `session_start()` differently. The numbered
walkthrough has `detect_kind()` return `other` as a fallback, and step 4
has `register()` use that result directly. The `pending`/`other`
subsection and the mermaid diagram both state the MCP server always
registers as `pending-<pid>` at startup, with the kind settled later
through the `initialize` handshake or an explicit `register()` call. It is
unclear whether `register()` uses `detect_kind()`'s result immediately at
startup, or whether startup always registers `pending` first.
