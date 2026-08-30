You are agent "{{me}}" on a message bus. Your partner is "{{peer}}".

To SEND the value X, run exactly this bash command:
  {{cli}} send {{peer}} -m "X" --summary "X" --from-name {{me}}

Setup:
1. Start a supervised process with your hub tool:
     op: "start", name: "{{watch}}", application: "sh",
     args: ["-c", "exec {{cli}} watch --name {{me}}"]
   This process prints nothing until mail arrives -- that is correct, not a
   fault. If you attach a `ready` check on its log, it WILL report "not
   ready" after your timeout, because there is nothing to match yet. A
   readiness timeout here is not a failure: proceed to step 3 regardless of
   whether it reports ready.
{{opener}}

Then repeat, and do nothing else:
3. Block until mail arrives, with your hub tool:
     op: "logs", name: "{{watch}}", follow: true, timeout: 300
   Pass no cursor. It returns only output that appears after the call starts,
   and it blocks until then. That is intended.
4. Every line it returns looks like:
     [agent-bus] from=<name> id=<id> summary=<VALUE>
   Send exactly one reply per line, chosen by VALUE:
     - a number less than {{last}} -> SEND that number plus one
     - the number {{last}}         -> SEND DONE
     - DONE                        -> SEND ACK, then stop entirely
     - ACK                         -> stop entirely, send nothing
5. Unless you stopped in step 4, go back to step 3.

Do not use sleep. Do not use `op: "wait"` — its pattern re-matches output you
have already seen, so it returns instantly forever.

If any command fails, stop and say FAILED plus the error. Do not improvise, do
not register yourself, do not retry with different arguments.

Say nothing else. Do not ask questions.
