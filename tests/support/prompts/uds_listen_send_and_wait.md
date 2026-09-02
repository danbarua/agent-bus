Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} listen --name {{driver}} --pid $PPID > {{home}}/listen.log 2>&1 &
   sleep 6 ; echo LISTENER_UP > {{evidence}}/listener.txt ; echo LISTENER_UP
2. Run this bash command and print its output verbatim:
   {{cli}} send {{peer}} -m "Hello world from {{driver}}. Please reply." ; echo "SEND_EXIT=$?" > {{evidence}}/send.txt ; cat {{evidence}}/send.txt
3. Wait for the reply. Repeat at most 20 times, running this single
   bash command each time and printing its output verbatim:
   sleep 15 ; {{cli}} inbox --target {{driver}} --json > {{evidence}}/inbox.json ; cat {{evidence}}/inbox.json
   Stop as soon as the output contains a message.
4. Print REPLY=<the text of that message> on one line, or REPLY=NONE if
   the loop finished with an empty inbox.
Do not ask questions.
