Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} join --name {{driver}} --kind other --pid $PPID --json > {{evidence}}/join.json ; echo "JOIN_EXIT=$?" ; cat {{evidence}}/join.json
2. Run this bash command and print its output verbatim:
   sleep {{stay_seconds}} ; echo STAYED
3. Print DONE.
Do not ask questions.
