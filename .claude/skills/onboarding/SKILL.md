---
name: onboarding
description: Observe agent-bus working, for real, before touching its code or docs. Invoke at the start of a session that will work on agent-bus itself.
---

# Onboarding: observe it, don't infer it

This is for a coding agent about to work **on** agent-bus — not something
agent-bus ships to its own consumers. It exists because of a specific,
repeated failure: an agent reads source or a doc, writes a confident sentence
about behavior it never ran, and the next session inherits that sentence as
settled fact. That happened to the exact question of whether `hub` pushes
into an idle omp session — proven working, documented wrong, silently
re-broken, on different days, more than once.

**Rule zero, and it governs every step below:**

> Do this. Observe this. If you do not observe it exactly as stated — **stop**.
> Do not infer why. Do not "fix" anything. Ask the user directly what's
> different before doing anything else.

A gap between what this file says and what you observe is not yours to
resolve by guessing. It is the most valuable thing you will find all session
— it means either this file is stale or something regressed — and either way
the fix is to ask, not to paper over it with a plausible-sounding sentence.

Every step below is free (CLI-only, no model spend) except step 6, which is
not, and should not be skipped on that account. The cost of a real run is a
rounding error next to the cost of another day of confidently wrong
documentation — that is the entire reason this file exists.

## Step 1 — identity costs nothing

```sh
export AGENT_BUS_HOME=$(mktemp -d)
uv run agent-bus register --name onboard-a --kind other
uv run agent-bus list --json
```

**Observe:** `onboard-a` appears in the list, live.

## Step 2 — sending and receiving are two different calls

```sh
uv run agent-bus register --name onboard-b --kind other
uv run agent-bus send onboard-b -m "hello" --summary "hi" --from-name onboard-a
uv run agent-bus inbox --name onboard-b
```

**Observe:** the message shows up, unread, under `onboard-b`'s inbox — not
`onboard-a`'s.

## Step 3 — `watch` emits a notice, never a body

In one shell:

```sh
uv run agent-bus watch --name onboard-b
```

In another (same `AGENT_BUS_HOME`):

```sh
uv run agent-bus send onboard-b -m "the full text of this message, which should not appear on the watch line" --summary "notice test" --from-name onboard-a
```

**Observe:** the watch line is `[agent-bus] from=onboard-a id=<id> summary=notice test` — the id and the summary, and *nothing else*. The body is not there. If you see the body on that line, `watch.py`'s own docstring is wrong and something regressed — stop and say so.

## Step 4 — fetching the body is a separate, deliberate act

```sh
uv run agent-bus read <id-from-step-3>
```

**Observe:** the full body arrives now, and only now. Then check the MCP
surface has the same operation: `grep -n '"name": "read_message"' src/agent_bus/mcp_server.py`. If it is missing, #152 has regressed — CLI and MCP have
drifted apart again, silently.

## Step 5 — Claude needs nothing, which is not the same as having nothing

If a real Claude Code session is running anywhere on this machine, check
whether it is visible without ever having called `agent-bus register`:

```sh
uv run agent-bus list --json | grep '"kind": "claude"'
```

**Observe:** any live Claude session appears via discovery alone. This is the
asymmetry `identity-and-peering.md` opens with — verify it rather than take
the doc's word for it. Don't write "Claude has no agent-bus" anywhere; the
true claim is "Claude *needs* none of it," and those are different sentences
with different truth values the moment someone wires it up anyway.

## Step 6 — omp's real shape, run it, don't re-derive it

Do not read `docs/harnesses/omp.md` and reason from there about whether `hub`
pushes into an idle session. Run the actual test:

```sh
./spendy_tests.sh two_agents -k omp
```

**Observe:** it passes, and read *how* it passes —
`tests/support/mail_woken_peer.py`'s `_spawn_omp` and
`tests/support/prompts/conversation_peer_park.md`. The shape is: `hub start`
once, then a **bounded** `hub op:"logs" follow:true timeout:300` loop, reading
and acting between calls. Not one call. Not an unbounded block. Not genuine
push into an idle session — that was tested directly (a real omp session,
told to make one `hub start` call and then stop completely, never woke) and
falsified.

If this test is failing, or missing, or someone has "simplified" it back down
to two harnesses: that is #133/#135/#152's exact regression happening again.
Stop, do not silently work around it, tell the user.

## Step 7 — now read the docs, with something to check them against

`docs/README.md` gives the mandatory order. Reading it *first*, with nothing
real to compare it to, is exactly how confidently-wrong prose gets inherited
instead of caught. You've now watched the mechanism it describes; read it as
a check on what you saw, not as the source of truth.

## Before you write anything down

Ask yourself, honestly, before any sentence goes into a doc, a commit message,
or an issue: **did I just observe this, or did I read code and infer it?**

If inferred: say so explicitly, or go observe it first. A doc that states an
inference with the same confidence as an observation is indistinguishable from
one that is simply wrong, and the next session cannot tell the difference
either.
