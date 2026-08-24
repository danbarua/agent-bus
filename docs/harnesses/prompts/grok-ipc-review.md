# Prompt: Grok Build socket and inter-agent messaging review

The prompt that produced `docs/harnesses/grok-build-ipc-reference.md`. Filed after the
fact for provenance and reuse — see `codex-ipc-review.md` for the same structure
applied to Codex, and reuse whichever is closer to the next target.

The parts that made the output good, and that are worth keeping in any variant:
mandatory `file:line` citations, **NOT FOUND** stated rather than a plausible
implementation described, `INFERRED` labels on anything derived, and a
confidence rating per section. The Grok review's most useful results were two
firm negatives and one security finding, none of which a softer prompt would
have surfaced.

A companion prompt covering the `monitor` tool and MCP notifications produced
`docs/harnesses/grok-build-monitor-reference.md`; it followed the same rules with
harness-specific questions.

---

**Task: document Grok Build's socket and inter-agent messaging implementation.**

You are reviewing the **Grok Build** source to produce a factual reference.
Describe what the code *does*, not what it should do. Mark anything inferred as
inferred, and cite `file:line` for every claim.

Starting point: the `grok` CLI has a `--leader-socket <PATH>` flag, default
`~/.grok/leader.sock`, described as "Use a custom leader socket path instead of
the default". This suggests a leader/daemon process with an IPC surface. That is
the only confirmed fact — everything else is open.

Answer these, and say explicitly when the answer is "this does not exist":

1. **Leader socket.** What creates it, what protocol does it speak (framing,
   encoding, handshake), and what operations does it expose? Is there
   authentication, and if so what form — token file, peer credentials,
   filesystem permissions?
2. **Session registry.** Does Grok maintain a discoverable record of running
   sessions, comparable to `~/.grok/active_sessions.json`? What fields does it
   carry, who writes them, how is liveness determined, and how are stale entries
   reaped?
3. **Inter-agent messaging.** Can one Grok session address another? If so: how
   is a target named or addressed, what is the message envelope, is delivery
   acknowledged, and does anything persist when the target is absent?
4. **Identity and naming.** How does a session acquire a name or id? Can it be
   renamed, and if so what happens to the old name? Is there any equivalent of a
   grace period for stale names?
5. **Presence and state.** Does a session publish status (idle/working/etc.),
   cwd, or similar? What updates it, and how often?
6. **Hooks and env.** Which environment variables does a Grok session export to
   child processes and to MCP servers, and which are hook-scoped versus ambient?
   This matters because an ambient variable can leak into an unrelated child and
   let it impersonate the session.

Deliverable: a single markdown document with a section per question, `file:line`
citations, and a short "confidence" note per section. Include exact protocol
shapes (JSON frame examples) where you can read them from source. If a subsystem
is absent, say so plainly rather than describing what a reasonable
implementation would look like.

Do not modify any files. Read-only review.
