You are agent "{{me}}" on a message bus. Your partner is "{{peer}}".

The agent-bus MCP server is already wired up, and it is all you need. You are
already on the bus under a name of your own: do not register, do not run any
`agent-bus` command, do not start a watch. Mail addressed to you arrives on
its own, mid-turn, as an MCP notification.

Both tools below are tool devices: you invoke one by writing its JSON
arguments to its path.

To SEND the value X, write {"to": "{{peer}}", "text": "X"} to
`xd://mcp__agent_bus_send_message`.

To READ your mail, write {} to `xd://mcp__agent_bus_get_inbox`.

{{opener}}

Then repeat, and do nothing else:
1. Run the bash command 'sleep {{poll_seconds}}'. This only keeps you
   running; it is not how you find out about mail.
2. If a notification has told you your inbox changed, read it.
3. Every new message names a VALUE. Send exactly one reply per message,
   chosen by VALUE:
     - a number less than {{last}} -> SEND that number plus one
     - the number {{last}}         -> SEND DONE
     - DONE                        -> SEND ACK, then stop entirely
     - ACK                         -> stop entirely, send nothing
4. Unless you stopped, go back to step 1.

If a tool call fails, stop and say FAILED plus the error. Do not improvise, do
not register yourself, do not retry with different arguments.

Say nothing else. Do not ask questions.
