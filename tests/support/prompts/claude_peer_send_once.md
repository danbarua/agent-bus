You are a peer in an integration test for agent-bus.

Do exactly this, then stop:

1. Use your ListAgents tool. One of the agents is called {{target}}.
2. Use your SendMessage tool to send it exactly this text:
   {{outbound}}
3. Say SENT.

Then wait, and do nothing further. Messages may arrive in your conversation as
<cross-session-message> blocks. You do not need to reply to them.
