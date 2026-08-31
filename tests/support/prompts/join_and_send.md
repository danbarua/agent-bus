Do exactly this, nothing else.
1. Run this single bash command -- both steps chained in one invocation, so
   there is no pause between them -- and print its output verbatim:
   {{cli}} join --name {{driver}} --kind other --pid $PPID --json > {{evidence}}/join.json ; echo "JOIN_EXIT=$?" ; cat {{evidence}}/join.json ; {{cli}} send {{peer}} -m "Hello world from {{driver}}" ; echo "SEND_EXIT=$?" > {{evidence}}/send.txt ; cat {{evidence}}/send.txt
2. Print DONE.
Do not ask questions.
