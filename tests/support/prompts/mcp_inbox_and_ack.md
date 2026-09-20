Do exactly this, nothing else.
1. Call the agent-bus MCP tool `self` with no arguments. Then print exactly
   one line: SELF=<the name field of the result, verbatim>.
2. Call the agent-bus MCP tool `list_agents` with no arguments. Then print
   exactly one line: SEEN=yes if the result includes an entry whose name is
   exactly "{{sender}}", otherwise print exactly SEEN=no.
3. Call the agent-bus MCP tool `get_inbox` with no arguments. The result
   holds exactly one message. Then print exactly one line:
   TEXT=<that message's text field, verbatim, nothing else on the line>.
4. Call the agent-bus MCP tool `ack_message` with message_id set to that
   message's id field. Then print exactly one line:
   ACKED=yes if the result's acked field is true, otherwise print exactly
   ACKED=no.
5. Print DONE.
Do not ask questions.
