# docs/harnesses

This directory holds one file per subject. Each file holds what is known
about one coding harness.

`<harness>.md` is the file to read when that harness misbehaves. It holds
the handful of facts that explain most of its failures. `<harness>-<thing>.md`
describes the detailed behavior of one mechanism. It is established by
reading source and probing the running binary.

This directory has no index file. `ls` lists the files, and git keeps that
listing current.

## Rule for adding a file

A file belongs here if it describes behavior that holds whether or not
agent-bus exists. The reference documents carry a
`<!-- Provenance: external read-only source review of ... -->` header. That
header names the checkout the document was read from. That header is the
test for belonging in this directory.

Our own design, decisions, and protocol stay in `docs/`. The dividing line
is the document's subject. `claude-code-presence.md` describes how Claude
Code works. `UDS-protocol.md` describes how this project speaks to Claude
Code. Both describe the same wire. Only `UDS-protocol.md` describes this
project.

## Before investigating a harness again

Read the file here first. Each file cites `file:line` against checkouts in
`~/Code/agents/`.

Then probe the running system too. Source code tells you the names. It does
not tell you the wire behavior. Grok's source names the roster method
`x.ai/sessions/list`. The wire expects `_x.ai/sessions/list`. The documented
name returns `-32601`.
