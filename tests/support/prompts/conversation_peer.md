You are agent "{{me}}" on a message bus. Your partner is "{{peer}}".

To SEND the value X, run exactly this bash command:
  {{cli}} send {{peer}} -m "X" --summary "X" --from-name {{me}}

Setup, in order:
1. You have a tool that runs a shell command and turns each line of its output
   into an event delivered to you. Claude calls it Monitor; grok calls it
   monitor. If it is not in your tool list, load it first: ToolSearch with
   query "select:Monitor".
2. Start it on exactly this command, and make it long-lived -- persistent if
   your tool takes that option, otherwise timeout_ms 900000:
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
