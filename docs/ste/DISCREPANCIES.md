# Discrepancies

Claims that looked internally inconsistent or under-specified when this
mirror was first written, resolved against `src/` and `docs/` directly.

## Resolved: not actually inconsistent

- **`docs/UDS-protocol.md` §7, TRACE emitting `DEBUG` severity.** Deliberate.
  `src/agent_bus/log.py:121-126`: `_SEVERITY = {"TRACE": "DEBUG"}`. Cloud
  Logging has no TRACE severity; DEBUG is the nearest one, and unambiguous
  because nothing else ever emits at DEBUG.
- **`docs/structured-logging.md` "Levels": `default` "is the level everything
  runs at" vs. `off` suppressing even failures.** Four mutually exclusive
  selections, not layers. Unset resolves to `default` (failures only,
  `log.py:20`); explicitly selecting `off` (or `none`/`silent`/`quiet`/`no`/
  `0`) means nothing at all, including failures. "Default" describes only
  the unset case.
- **`docs/identity-and-peering.md`: does registration use `detect_kind()`'s
  result immediately, or register `pending` first?** Both, on two different
  entry points. `lifecycle.session_start()` (hooks, plain CLI use) registers
  `detect_kind()`'s result directly — settles as `other` immediately if
  nothing matches (`lifecycle.py:134-145`). `mcp_server._startup_identity()`
  (the MCP-server-only path) overrides an initial `other` to `pending`
  instead, because an MCP client hasn't said hello yet (`mcp_server.py:
  506-523`).
- **`docs/harnesses/omp.md`: does the `initialize`-handshake path produce
  the same `kind` as reading omp's own daemon-client files?** Yes, the same
  string. `identify_mcp_client()` returns `("omp", None)` for
  `clientInfo.name == "omp-coding-agent"`
  (`src/agent_bus/adapters/lifecycle/__init__.py:81-82`).

## Resolved: fixed in this mirror

- **`docs/running-the-bridge.md`'s "alias" (a role, one live holder) vs. the
  glossary's "alias" (address reconciliation).** Both real; same field
  (`aliases: list[str]`), enforced differently. `GLOSSARY.md`'s `alias` entry
  now states both.
- **`docs/running-the-bridge.md`'s "outbox", used once, against every other
  file's "inbox".** Not a typo — `outbox` names a real, separate cloud-side
  queue (`cloud/store.py:34`). Added to `GLOSSARY.md`.

## Open: a real inconsistency in the source

- **`docs/hooks-in-foreign-harnesses.md`**: the prose says `session_start`
  "is close to" taking a `SessionDescriptor`; three lines later, the vendor
  table says it already does. The table is right —
  `lifecycle.session_start()` (`lifecycle.py:123-128`) already takes an
  explicit `descriptor: SessionDescriptor | None` parameter. The prose is
  stale, left over from before the table row was struck through and
  updated. This is in `docs/`, not this mirror; needs fixing at the source.

## Open: trivial wording

- **`docs/harness-compatibility.md`**, "the easiest of the three" has no
  stated antecedent nearby. Codex, Grok, and omp (the MCP-integrated three,
  contrasted with Claude and pi) — needs naming, not investigation.
