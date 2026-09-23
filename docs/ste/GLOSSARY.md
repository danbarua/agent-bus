# Glossary

One term for one thing. Use these words and no synonyms for them across
`docs/ste/`.

| Term | Meaning |
|---|---|
| **bus** | The shared store at `AGENT_BUS_HOME` (default `~/.agent-bus`). It holds the roster and one inbox per agent. |
| **roster** | The list of registered agents. Each row carries an id, a name, a kind, and a status. |
| **inbox** | One JSONL file per agent that holds the messages sent to it. |
| **outbox** | A cloud bridge's queue of messages waiting to be relayed out to it. A separate store from `inbox`, keyed the same way (`kind:name`). `cloud/store.py:34` defines `INBOX, OUTBOX = "inbox", "outbox"`. |
| **listener** | A process that accepts incoming connections for a peer, so a reply has somewhere to arrive. Claude Code runs its own; agent-bus starts one for every other kind. |
| **peer** | An agent reachable through the bus, addressed by name or id. |
| **kind** | The harness an agent runs on: `claude`, `codex`, `grok`, `omp`, or `other`. `other` means the agent is registered and no discovery adapter identified its harness. `pi` peers have kind `other`; no discovery adapter identifies `pi` as a harness. |
| **alias** | A second address recorded for one roster entry, so two addresses reconcile into one row. A bridge address is an alias that also enforces one live holder: `src/agent_bridge/bridge.py:206-242` refuses to register a role another live pid already claims. |
| **notice** | The short arrival record a watch delivers: who a message is from and enough of the summary to judge urgency. It is not the message body. |
| **watch** | A standing command, `agent-bus watch`, that reports each notice as it arrives instead of polling for mail. |
| **transport** | The code path that delivers a message to a specific kind of peer, selected by the target's kind. |
| **session file** | The JSON file a harness or listener publishes to record a live session, for example `~/.claude/sessions/<pid>.json`. |
| **file bus** | The default transport. It delivers a message by writing to the target's inbox file. A kind with no native transport uses the file bus. |
| **dial-back** | A new outbound connection agent-bus opens back to a sender's own socket, used only to deliver a status frame. |

## Register notes

- Sentences: one topic each, 20 words or fewer.
- Paragraphs: 6 sentences or fewer.
- No em-dashes; split the sentence instead.
- No rhetorical absolutes (`nothing`, `nobody`, `everything`, `exactly`,
  `literally`, `full stop`, `the whole of it`). State the scoped claim.
- No intent-by-negation (`X is not Y, it is Z`, `deliberately`, `on purpose`,
  `rather than`). State what the code does.
