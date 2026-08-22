---
name: agent-bus-send
description: Use when the user runs /agent-bus-send or asks to send a message on the agent-bus file bus to another agent.
argument-hint: "<name-or-id> -m <text>"
user-invocable: true
disable-model-invocation: true
---

# agent-bus-send

Follow **agent-bus** and the consent rule.

1. If the target is missing, call MCP `list_agents` and pick a live name or id.
2. Confirm the message text with the user if it was not given.
3. Call MCP `send_message` with `to`, `text`, and optional `summary`.
4. Report the sent id. Do not claim the recipient has read it until they ack.
