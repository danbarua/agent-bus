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

## `docs/harnesses/omp.md`, "Kind detection"

The source states an MCP child launched by omp is not detected by
`detect_kind()`. It registers as `pending-<pid>` and is later named through
the `initialize` handshake, which reports `omp-coding-agent`. The source
does not state what `kind` value the entry carries after that handshake.
Separately, `docs/identity-and-peering.md` states discovery reads omp's own
daemon-client files directly and reports `kind: omp` without needing the
handshake. It is unclear whether these are the same path or two different
paths to the same result.

## `docs/structured-logging.md`, "Levels"

The source states the `default` level "is the level everything runs at,"
but also documents `off` and other levels as explicitly selectable, and
states that selecting `off` suppresses even default-level failure logging.
These two claims do not reconcile: either the default level is not
unconditional, or a selected level does not fully override it.

## `docs/running-the-bridge.md`

The source uses "outbox" once, for where the cloud relays a push for a
`remote` pairing. Every other passage in this file, and the glossary, use
"inbox" for the same kind of per-agent mail queue. "Outbox" is not a
glossary term.

## `docs/hooks-in-foreign-harnesses.md`

The source's prose states core's `session_start` "is close to this
already," implying it does not yet fully match the target shape. The
source's vendor-location table states core's `session_start` "now takes a
`SessionDescriptor`," implying it does match. These two claims do not
reconcile.
