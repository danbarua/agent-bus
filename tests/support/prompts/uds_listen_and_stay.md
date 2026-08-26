Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} listen --name {{driver}} --pid $PPID > {{home}}/listen.log 2>&1 &
   sleep 6 ; echo LISTENER_UP > {{evidence}}/listener.txt ; echo LISTENER_UP
2. Run this bash command and print its output verbatim:
   sleep {{stay_seconds}} ; {{cli}} list --json > {{evidence}}/list.json ; echo LIST_TAKEN
3. Print DONE.
Do not ask questions.
