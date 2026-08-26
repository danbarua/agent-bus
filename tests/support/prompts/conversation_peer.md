You are agent "{{me}}" on a message bus. Your partner is "{{peer}}".

To SEND the value X, run exactly this bash command:
  {{cli}} send {{peer}} -m "X" --summary "X" --from-name {{me}}

Setup, in order:
1. If you do not have a Monitor tool, load it: ToolSearch, query "select:Monitor".
2. Start Monitor with timeout_ms 900000 on exactly this bash command:
   {{cli}} watch --name {{me}}
{{opener}}
4. Then STOP. Do not poll. Do not sleep. Wait for monitor events.

A monitor event looks like:
  [agent-bus] from=<name> id=<id> summary=<VALUE>

After setup you send ONLY in reply to a monitor event, one send per event,
choosing by VALUE:
  - a number less than {{last}} -> SEND that number plus one
  - the number {{last}}         -> SEND DONE
  - DONE                        -> SEND ACK, then stop
  - ACK                         -> stop, send nothing

If any command fails, stop and say FAILED plus the error. Do not
improvise, do not register yourself, do not retry with different
arguments.

Say nothing else. Do not ask questions.
