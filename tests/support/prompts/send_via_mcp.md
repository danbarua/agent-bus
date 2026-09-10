Do exactly this, nothing else.

Send one message on agent-bus to "{{target}}" with text "hello from an agent
that never registered", using the agent-bus MCP server's `send_message` tool.

If your harness exposes MCP tools as tool devices rather than as named tools,
that tool is `xd://mcp__agent_bus_send_message` and you invoke it by writing
its JSON arguments to that path: {"to": "{{target}}", "text": "hello from an
agent that never registered"}.

Do not call `register`. Do not run any shell command. Do not ask questions.
