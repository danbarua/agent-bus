Do exactly this, nothing else.
1. Call the agent-bus MCP tool `register` with name="{{name}}" and kind="{{kind}}".
2. Call the agent-bus MCP tool `send_message` with to="{{target}}" and text="hello from {{name}}".
3. Print exactly JOINED=<the name field from step 1's result>.
Do not ask questions.
