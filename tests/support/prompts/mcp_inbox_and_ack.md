Do exactly this, nothing else.
1. Call the agent-bus MCP tool `list_agents` with no arguments. Then print
   exactly one line: SEEN=yes if the result includes an entry whose name is
   exactly "{{driver}}", otherwise print exactly SEEN=no.
2. Call the agent-bus MCP tool `get_inbox` with name="{{driver}}". The
   result holds exactly one message. Then print exactly one line:
   TEXT=<that message's text field, verbatim, nothing else on the line>.
3. Call the agent-bus MCP tool `ack_message` with message_id set to that
   message's id field and name="{{driver}}". Then print exactly one line:
   ACKED=yes if the result's acked field is true, otherwise print exactly
   ACKED=no.
4. Print DONE.
Do not ask questions.
