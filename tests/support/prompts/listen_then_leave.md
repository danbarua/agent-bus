Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} listen --name {{driver}} --pid $PPID > {{home}}/listen.log 2>&1 &
   echo $! > {{evidence}}/listener.pid
   sleep 6 ; {{cli}} list --json > {{evidence}}/before.json ; echo LISTENER_UP
2. Run this bash command and print its output verbatim:
   {{cli}} leave --name {{driver}} --json > {{evidence}}/leave.json 2>&1 ; echo "LEAVE_EXIT=$?"
   sleep 4
   if kill -0 $(cat {{evidence}}/listener.pid) 2>/dev/null ; then echo STILL_RUNNING > {{evidence}}/after.txt ; else echo STOPPED > {{evidence}}/after.txt ; fi
   {{cli}} list --json > {{evidence}}/after.json
   cat {{evidence}}/after.txt
3. Print DONE.
Do not ask questions.
