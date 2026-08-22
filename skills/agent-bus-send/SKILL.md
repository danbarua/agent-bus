---
name: agent-bus-send
description: Use when the user runs /agent-bus-send or asks to send a message on the agent-bus file bus to another agent.
argument-hint: "<name-or-id> -m <text>"
user-invocable: true
disable-model-invocation: true
---

# agent-bus-send

Follow **agent-bus** for CLI path and the consent rule.

1. If the target is missing, run `agent-bus list --json` and pick a live name or id.
2. Confirm the message text with the user if it was not given.
3. Run `agent-bus send <NAME_OR_ID> -m "<text>" --summary "<short>"`.
4. Report the sent id. Do not claim the recipient has read it until they ack.
