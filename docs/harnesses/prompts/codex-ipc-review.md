# Prompt: Codex inter-agent messaging design review

Hand this to a cloud Claude agent with access to `openai/codex`. It produces a
source reference in the same shape as `docs/harnesses/grok-build-ipc-reference.md` and
`docs/harnesses/grok-build-monitor-reference.md`.

Filed so the method is reproducible: those two documents are good because the
prompt demanded `file:line` citations, firm negatives, explicit `INFERRED`
labels and per-section confidence. Reuse that structure for any further harness
review — swap the entry point and the harness-specific questions.

Triggered by <https://github.com/openai/codex/pull/39092>, which appears to add
session-addressed messaging with a queue — the first of the three harnesses to
have store-and-forward, if it holds up.

---

**Task: design discovery dive on OpenAI Codex's inter-agent messaging.**

You are producing a factual source reference for the `openai/codex` repository.
Describe what the code **does**, not what it should do. This document will be
used to decide how a separate project aligns with Codex's existing mechanisms
rather than reinventing them, so precision matters more than breadth.

**Context for relevance judgements.** The requesting project, `agent-bus`, makes
non-Claude coding agents (grok, omp, codex) appear as native peers to Claude
Code — discoverable in its `ListAgents` and messageable with its `SendMessage`,
over Claude's own Unix-domain-socket peer protocol. We have already reviewed
Grok Build the same way. Two findings there shaped the questions below: Grok's
local shell has **no** session-to-session messaging at all, and its leader
socket has **no** authentication. We want to know where Codex sits on both axes.

**Entry point.** <https://github.com/openai/codex/pull/39092> — "queue messages
for existing sessions". From the PR description it adds
`codex queue --thread <THREAD> --message <TEXT>`, submitting via a
`thread/queue/add` app-server API; resolves sessions by UUID or exact name
across interactive/exec/custom sources; rejects ambiguous or duplicate names;
supports local and explicit remote app servers; rejects empty messages and image
attachments. **Treat all of that as unverified hearsay** — it came from a
summary of the PR page, not the code. Confirm or correct each claim against the
source.

Start there, then expand to the surrounding subsystem. Answer these, and say
explicitly when the answer is "this does not exist":

1. **The app-server.** What is it — a daemon, a per-session process, something
   else? How is it addressed (Unix socket, TCP, named pipe, HTTP)? What is the
   framing and encoding? Is there a handshake or version negotiation? What is
   its lifecycle: who spawns it, how is leader/singleton election handled, what
   happens to a stale socket?
2. **Authentication and trust.** Does anything authenticate a client to the
   app-server — peer credentials, a token file, socket permissions, an explicit
   `chmod`? If the answer is "filesystem permissions only", say so plainly; that
   is a finding, not a gap in your research. Distinguish local from remote
   app-server auth.
3. **`thread/queue/add` and the wider API.** Enumerate the full method surface,
   not just this one. For each: request/response envelope with exact field
   names, and a real example frame if you can read one from tests. Are responses
   acknowledged, and how are errors returned?
4. **Delivery semantics — the question we care most about.** When a message is
   queued for a session: does it persist to disk, or live in memory? What
   happens if the target is busy, idle, stale, or exited? Is it delivered
   at-most-once, at-least-once, or exactly-once? Is there redelivery, expiry, or
   a dead-letter path? Does the sender learn the outcome?
5. **Session registry, discovery and liveness.** How does `codex queue` resolve
   a thread by UUID or by name? Where does that registry live — file, socket
   query, in-memory? What fields does an entry carry (pid? cwd? status? socket
   path?)? How is liveness determined, and how are stale entries reaped? What
   are "interactive", "exec" and "custom" sessions, and how do they differ?
6. **Identity and naming.** Thread id versus human name: which is immutable? How
   is a name assigned, and can it be changed? On rename, is the old name
   retained, aliased, or discarded? The PR reportedly rejects ambiguous or
   duplicate names — quote that logic and any collision-resolution rule.
7. **Presence and status.** Does a session publish state
   (idle/working/needs-input/stale)? What is the exact enum? Who updates it, on
   what trigger, and how often — event-driven or timed? Is it readable by
   another process?
8. **Inbound surfacing.** How does a queued message actually reach the target
   session's conversation? Is it injected as a turn, an interjection, a system
   reminder? Is it idle-gated or immediate? Does it wake an idle session, and if
   so how?
9. **A monitor/watch equivalent.** Does Codex have anything letting a session
   watch a stream of external events (compare Grok's `monitor` tool, which turns
   each stdout line of a shell command into a conversation event with rate
   limiting)? If not, say NOT FOUND.
10. **Environment variables and child-process scoping.** Which variables does a
    Codex session set for hook and MCP child processes, and which are
    process-wide (`set_var`/`putenv`) versus scoped to a single spawn? Are
    credentials or socket paths scrubbed before spawning untrusted children?
    This matters because an ambient variable inherited by an unrelated child can
    let it impersonate the session or reach a privileged socket — that is a real
    finding in the Grok review.

**Method and output requirements.**

- Read-only. Do not modify, build, or run anything.
- **Every behavioural claim needs a `file:line` citation** against a stated
  commit/HEAD. Record the repo, branch and HEAD you reviewed at the top.
- **State NOT FOUND rather than describing what a reasonable implementation
  would look like.** A well-evidenced negative — "I searched X, Y, Z for this
  and it does not exist" — is a first-class result and often the most valuable
  one. Say what you searched.
- **Label anything inferred as `INFERRED`.** Combining confirmed facts into a
  consequence is fine; presenting it as directly stated is not.
- Quote exact struct/enum/constant names and real wire shapes. Prefer verbatim
  short code blocks over paraphrase.
- Give a **confidence rating per section**, with the reason.
- Flag any claim in the PR summary above that turns out to be wrong.
- Note where a subsystem was sampled rather than exhaustively swept, so the
  boundary of verification is visible.

**Deliverable:** one markdown document, a section per question, in that order.
