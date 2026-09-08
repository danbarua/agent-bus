You are agent "{{me}}" on a message bus. Your partner is "{{peer}}".

To SEND the value X, run exactly this bash command:
  {{cli}} send {{peer}} -m "X" --summary "X" --from-name {{me}}

1. Nothing to arm. Mail addressed to you arrives on its own as an MCP
   notification -- no watch process, no tool to start.
{{opener}}

Then repeat, and do nothing else:
3. Run the bash command 'sleep {{poll_seconds}}'.
4. If an MCP notification told you your inbox changed since you last looked,
   read it. If nothing changed, go back to step 3.
5. Every new message names a VALUE. Send exactly one reply per message,
   chosen by VALUE:
     - a number less than {{last}} -> SEND that number plus one
     - the number {{last}}         -> SEND DONE
     - DONE                        -> SEND ACK, then stop entirely
     - ACK                         -> stop entirely, send nothing
6. Unless you stopped in step 5, go back to step 3.

If any command fails, stop and say FAILED plus the error. Do not improvise, do
not register yourself, do not retry with different arguments.

Say nothing else. Do not ask questions.
