Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} join --name {{driver}} --kind other --pid $PPID --json > {{evidence}}/join.json ; echo "JOIN_EXIT=$?" ; cat {{evidence}}/join.json
2. Run this bash command IMMEDIATELY after step 1, with no sleep and no
   delay of any kind, and print its output verbatim:
   {{cli}} send {{peer}} -m "Hello world from {{driver}}" ; echo "SEND_EXIT=$?" > {{evidence}}/send.txt ; cat {{evidence}}/send.txt
3. Print DONE.
Do not ask questions.
