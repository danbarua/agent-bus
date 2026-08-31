Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} inbox --name {{driver}} --json > {{evidence}}/inbox.json ; cat {{evidence}}/inbox.json
2. The output is a JSON array holding exactly one message. Find its "id"
   field and use that value -- call it MSG_ID -- in place of MSG_ID below.
   Run this bash command and print its output verbatim:
   {{cli}} read MSG_ID --name {{driver}} --json > {{evidence}}/read.json ; cat {{evidence}}/read.json
3. Using the same MSG_ID, run this bash command and print its output
   verbatim:
   {{cli}} ack MSG_ID --name {{driver}} --json > {{evidence}}/ack.json ; cat {{evidence}}/ack.json
4. Run this bash command and print its output verbatim:
   {{cli}} inbox --name {{driver}} --json > {{evidence}}/inbox_after.json ; cat {{evidence}}/inbox_after.json
5. Print DONE.
Do not ask questions.
