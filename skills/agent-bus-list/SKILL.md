---
name: agent-bus-list
description: Use when the user runs /agent-bus-list or asks which agents are on the agent-bus roster.
argument-hint: "[--kind claude|grok|omp|codex|all]"
user-invocable: true
disable-model-invocation: true
---

# agent-bus-list

Follow **agent-bus** for CLI path.

Run `agent-bus list --json` (add `--kind` from $ARGUMENTS when the user names a harness). Show name, kind, pid, status, and id. Output is live roster ∪ native Claude/Grok/omp/Codex sessions.
