Do exactly this, nothing else.
1. Call the agent-bus MCP tool `register` with name="{{driver}}" and kind="{{kind}}".
2. Call the agent-bus MCP tool `set_status` with status="{{status}}".
3. Call the agent-bus MCP tool `self` with no arguments. Then print exactly
   one line: SELF=<its name field>,<its status field>
4. Call the agent-bus MCP tool `list_agents` with no arguments. Find the
   entry whose name is exactly "{{driver}}". Then print exactly one line:
   LISTED=<that entry's status field>
5. Print DONE.
Do not ask questions.
