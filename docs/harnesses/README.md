# docs/harnesses

One directory per subject: everything known about a given coding harness.

`<harness>.md` is what you want when that harness is misbehaving — the handful
of facts that explain most of its failures. `<harness>-<thing>.md` is the
detailed behaviour of one mechanism, established by reading source and probing
the running binary.

There is no index here. `ls` is the index, and git keeps it current.

## The rule for adding one

A file earns a place here if it describes **behaviour that is true whether or
not agent-bus exists**. The reference documents carry a
`<!-- Provenance: external read-only source review of ... -->` header naming
the checkout they were read from; that header is the test.

Our own design, decisions and protocol stay in `docs/`. The line is subject,
not topic — `claude-code-presence.md` is how Claude Code works, `UDS-protocol.md`
is how *we* speak to it. Same wire, and only the second describes this project.

## Before re-investigating a harness

Read the file here first. These took real effort and cite `file:line` against
checkouts in `~/Code/agents/`.

Then probe the running thing anyway. Source tells you the names; it does not
tell you the wire. Grok's source calls the roster method `x.ai/sessions/list`
while the wire wants `_x.ai/sessions/list`, and the documented name answers
`-32601`.
