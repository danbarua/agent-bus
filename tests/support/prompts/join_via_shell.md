Do exactly this, nothing else.
1. Run this bash command and print its output verbatim:
   {{cli}} register --name {{name}} --kind {{kind}} --pid $PPID
2. Run this bash command and print its output verbatim:
   {{cli}} send {{target}} -m "hello from {{name}}" --from-name {{name}}
3. Print exactly JOINED={{name}}
Do not ask questions.
