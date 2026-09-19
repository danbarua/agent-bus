# Structured logging

The field contract for agent-bus, agent-bridge, agent-bus-cloud, labkit and
exo-ledger. This file is the contract. `src/agent_bus/logevents.py` holds the
vocabulary; a test fails when the table below drifts from it.

## Read the logs

```sh
# what a bridge is running, and with what configuration
jq -c 'select(.message=="bridge_started")' ~/.local/state/agent-bus/agent-bridge.jsonl

# one message, both sides of the bridge/cloud boundary
grep '"trace_id": "<id>"' ~/.local/state/agent-bus/*.jsonl
gcloud logging read 'jsonPayload.trace_id="<id>"' --project <project>

# is the cloud reachable, and since when
jq -c 'select(.message|startswith("cloud_call_"))' ~/.local/state/agent-bus/agent-bridge.jsonl

# one bridge out of several sharing the file
jq -c 'select(.address=="desktop:claude")' ~/.local/state/agent-bus/agent-bridge.jsonl

# everything that is not routine
jq -c 'select(.severity!="INFO")' ~/.local/state/agent-bus/agent-bridge.jsonl
```

## Where it goes

    $XDG_STATE_HOME/agent-bus/<service>.jsonl     ~/.local/state when unset

`agent-bus.jsonl` and `agent-bridge.jsonl` sit side by side: one file per
service, never a directory. Several bridge processes share `agent-bridge.jsonl`;
`address` tells them apart. Ordering across services is by `time`, not by
position in a file.

| variable | effect |
|---|---|
| `AGENT_BRIDGE_LOG_FILE` | the bridge's file; checked first for `agent-bridge` |
| `AGENT_BUS_LOG_FILE` | every service, into the file it names |
| `AGENT_BUS_LOG_LEVEL` | `info` (unset), `warning`, `trace`, or `off` |

A file that cannot be opened falls back to stderr, never stdout: an MCP server
speaks JSON-RPC on stdout. `bridge_started` records the destination and level
the logger actually holds (`log_file`, `log_level`).

Human-readable output is a different stream and belongs wherever the OS puts a
service's stdout: `~/Library/Logs/<service>/` under launchd.

## The record

One JSON object per line. Every record starts with the same envelope keys, in
this order, and then the event's own fields in registry order. An envelope key
or event field with no value is absent, never `null`. The logger writes the
envelope; an event cannot
carry a key the registry does not name, and a message-scoped event cannot be
built without its message id.

<!-- fields:start -->
| field | type | written by | meaning |
|---|---|---|---|
| `time` | string | logger | ISO 8601 UTC, millisecond precision, fixed width |
| `severity` | string | logger | a Cloud Logging severity: DEBUG INFO WARNING ERROR CRITICAL |
| `service` | string | logger | which binary: agent-bus, agent-bridge, agent-bus-cloud |
| `adapter` | string | logger | how it was reached: cli, mcp, listen, bridge |
| `version` | string | logger | the build that wrote the line |
| `pid` | integer | logger | the writing process |
| `ppid` | integer | logger | its parent -- 1 means orphaned |
| `address` | string | logger | which bridge, when several share one file |
| `agent` | string | logger | the emitter's name on the bus |
| `kind` | string | logger | the emitter's harness kind |
| `client` | string | logger | the harness on the far end of an MCP handshake |
| `trace_id` | string | logger | the message id: one id, both sides of the boundary |
| `message` | string | logger | the event name; for a verb call, the verb |
| `verb` | string | event | what a caller asked for, never the transport |
| `op` | string | event | which cloud operation: roster, pull, push or ack |
| `status` | integer | event | HTTP status from the cloud, when there was one |
| `error` | string | event | the exception class -- filterable, never prose |
| `token_source` | string | event | environment, keychain, file or none |
| `version_source` | string | event | distribution or source-tree |
| `install` | string | event | installed, editable or source-tree |
| `reason` | string | event | why a process is stopping |
| `auto_reply` | boolean | event | whether the bridge answers each sender with a receipt |
| `to` | string | event | the recipient |
| `sender` | string | event | the originator of a message |
| `peer` | string | event | the declared relay partner |
| `name` | string | event | an agent's name on the bus |
| `topic` | string | event | a subscription topic |
| `gh_event` | string | event | the GitHub event name |
| `delivered_id` | string | event | the id of the local copy a delivery produced |
| `count` | integer | event | how many |
| `consecutive` | integer | event | failures in a row, this one included |
| `suppressed` | integer | event | failures since the last record of this outage |
| `failures` | integer | event | how many failures an outage held |
| `outage_seconds` | number | event | how long an outage lasted |
| `retry_in_seconds` | number | event | when the next attempt is due |
| `days` | number | event | days until a credential expires |
| `inbound_poll_seconds` | number | event | idle interval between cloud polls |
| `outbound_poll_seconds` | number | event | interval between local inbox drains |
| `since` | string | event | when an outage began, ISO 8601 UTC |
| `url` | string | event | the cloud endpoint |
| `spool_dir` | string | event | where a spooling bridge writes |
| `module_path` | string | event | where the running package was imported from |
| `executable` | string | event | the python interpreter |
| `python` | string | event | its version |
| `argv` | array | event | how the process was started |
| `log_file` | string | event | where this record is being written |
| `log_level` | string | event | the level in force |
| `error_message` | string | event | str(exception), capped; may name an agent, never a body |
<!-- fields:end -->

`service`, `adapter`, `address`, `agent`, `kind` and `client` are process
identity, set once with `logevents.identify()` and the same for every thread.
An `agent` and `kind` that are set are used as given; without them the logger
looks the process up on the bus for each record.

`adapter` is how the process was reached: `cli`, `mcp`, `listen`, `bridge`.
`ppid` of 1 means the process was orphaned. `version` is the build that wrote
the line, and a source tree with no installed distribution reports
`0+unknown` -- `bridge_started` says which case applies (`version_source`,
`install`).

`time` is ISO 8601 UTC. The agent-bus family writes millisecond precision at
fixed width so two records in one second still order; `agent-bus-cloud`
(`cloud/logs.py`) writes whole seconds.

Verb records written by agent-bus itself (`send`, `inbox`, `join`, ... from
`log.logged`) share the envelope and follow it with `verb`, `ok`, `ms`, `args`
and an `error` holding the exception message; `args` is a nested object and
may hold `null`s. `jq 'select(.verb)'` selects them.

## trace_id

**`trace_id` is the message id**, the same string on both sides of the
bridge/cloud boundary: a local message travels as the cloud's document id, and
the cloud's id names the local copy of a reply.

- An event about one message (`MessageEvent`) requires the id and writes it as
  `trace_id`.
- Work done on behalf of a message runs inside `logevents.bind_trace(id)`, and
  every record written inside -- including one from code that knows nothing
  about the message -- carries it. The binding is a ContextVar: a thread needs
  `contextvars.copy_context().run`.
- A webhook delivery is one record per (subscriber, source event):
  `trace_id` is the cloud's id for the source event, `delivered_id` the local
  message it went into. A digest of N events is one local message and N
  records sharing one `delivered_id`.
- Across a UDS frame the id does not carry: the frame's `msg_id` and the
  durable copy's id are different (`docs/UDS-protocol.md`).

A message outlives the HTTP request that carried one leg of it, so the message
id is the outer identifier and the Cloud Run request trace is a span within
it. The cloud emits both.

## Errors

`error` is the exception class, filterable and stable. `error_message` is
`str(exception)` capped at 1000 characters (`logevents.ERROR_MESSAGE_CAP`); it
says why a call failed and may name an agent, never a message body. `status`
is the HTTP status when the cloud answered (`CloudError.status`). A failure
event carries `error` and `error_message`, and `status` when there is one; a
timeout, a DNS failure and a 401 are told apart by those fields, not by
parsing prose.

## Outages

A cloud that is down fails every poll the same way. `agent_bridge/outage.py`
keeps one gate per cloud operation -- `roster`, `pull`, `push`, `ack` -- and
logs the run, not each failure:

| event | severity | when |
|---|---|---|
| `cloud_call_failed` | WARNING | the first failure, and again at failure 2, 4, 8, 16, ... |
| `cloud_call_refused` | ERROR | the same, when the cloud answered 400, 401, 403 or 404: waiting will not fix it |
| `cloud_call_recovered` | INFO | the first success after failures: `failures`, `outage_seconds`, `since` |

`consecutive` counts failures in a row, `since` is when the run began, and
`suppressed` is how many failures since the last record went unlogged, so up
to the last record `records + sum(suppressed)` is every failure. Four thousand
failures are twelve records. A change between transient and permanent is
recorded at once and does not restart the count.

The next attempt waits `min(300 s, interval * 2^(n-1))` with 20% jitter, where
`interval` is the loop's own interval at that moment. Only cloud calls wait:
the local inbox is drained every pass, and mail whose push is not yet due
stays unread. `once` always attempts.

## Levels

| `AGENT_BUS_LOG_LEVEL` | emits |
|---|---|
| unset, `info` | routine activity: what happened, to which message, and whether it worked |
| `warning` | warnings and errors only |
| `trace` | the firehose, message content included |
| `off`, `none`, `silent`, `quiet`, `no`, `0` | nothing, not even a failure |

An unrecognised value resolves to `info`: a typo must not silence logging.

Levels and severities are different axes. The level selects what is emitted;
`severity` is what a record carries, and its values are Cloud Logging's:
`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. There is no `TRACE` or `WARN`
severity, so a TRACE record carries `DEBUG` and nothing else emits there.

**Only TRACE may record message content.** Every other level measures a body
and never copies it: a log that copies message text is a second inbox with no
TTL. An event that declares a content field must be TRACE
(`test_no_event_outside_trace_may_declare_a_content_field`). TRACE is never
selected by accident and should not be left on.

**TRACE truncates.** A string is capped at 8 KB (`log.TRACE_FIELD_CAP`) and
the untruncated length is written beside it as `<field>_len`, present only
when the field was cut. The fields a call passes are bounded at 32 KB
(`log.TRACE_RECORD_CAP`); a field whose shape blows that -- a long list, a wide
dict -- is replaced by `{"_oversized": true, "_size": N, "keys": [...],
"keys_len": N}`. A single unbounded `write()` can split, and a split append
leaves a file `jq` dies halfway through.

## Startup and stop

`bridge_started` is written once when the bridge is up: `version_source`,
`install`, `module_path`, `python`, `executable`, `argv`, the poll intervals,
`log_file`, `log_level`, `name`, `auto_reply`, `peer`, and where it points --
`url` and `token_source` (the name of the source: `environment`, `keychain` or
`file`, never any part of the token), or `spool_dir` when it spools.
`bridge_stopped` is written on the way out with `reason` `exit`,
`interrupted` or `error`, and the failure's `error`, `error_message` and
`status` when it stopped on an exception. A bridge that failed before it was up
writes `bridge_not_started` and no `bridge_stopped`.

Every event agent-bridge writes is a class in `src/agent_bridge/events.py`.
`grep -h 'message: ClassVar' src/agent_bridge/events.py` lists the names.

## Adding a field or an event

An event is a frozen dataclass in `events.py` with `level`, `message` and
fields named in `logevents.FIELDS`, typed as the registry types them. A new
field is a new row in `FIELDS` with its one-line meaning, placed by the column
principle: closed-set categories, then who and what, then numbers, then free
text -- what does not change on the left. The conformance tests fail on a field
the registry does not own, a registry field no event writes, an event nothing
constructs, and a table out of step with this file.
`src/agent_bridge/` makes no `log.info`, `log.warn` or `log.trace` call, and
`tests/agent_bus/test_log_ratchet.py` holds the count in `src/agent_bus/` from
going up.

## Two things Cloud Logging will bite you on

**It reads `severity`, not `level`.** A line with `level: 30` is INFO forever,
however loudly it was logged. `pino` emits `level` by default -- override it.

**It nests on its own key, not on `trace_id`.** To fold app logs under the
request that produced them:

```json
"logging.googleapis.com/trace": "projects/<project-id>/traces/<trace-id>"
```

Emit both: `trace_id` for everyone else, the qualified form for GCP. On Cloud
Run the trace arrives as the `X-Cloud-Trace-Context` header
(`TRACE_ID/SPAN_ID;o=1`); the project id comes from configuration, because the
header does not carry it. `cloud/logs.py::trace_field` does exactly this.

## Per language

Standardise on the keys and use each ecosystem's idiomatic tool.

**Python** -- stdlib `logging` with a `Formatter` subclass: `src/agent_bus/log.py`
and `cloud/logs.py` share no code, because the package promises
`dependencies = []` and the server is a separate deployable.

**TypeScript** -- `pino`, with five overrides. The defaults are wrong in ways
that look right, and this config is run and its output checked:

```ts
import pino from "pino";

// pino's labels are not Cloud Logging's severities: `warn` and `fatal` are
// not valid LogSeverity values, and there is no TRACE.
const SEVERITY: Record<string, string> = {
  trace: "DEBUG", debug: "DEBUG", info: "INFO",
  warn: "WARNING", error: "ERROR", fatal: "CRITICAL",
};

export const log = pino({
  base: { service: "labkit" },
  messageKey: "message",                       // default is `msg`
  timestamp: () => `,"time":"${new Date().toISOString()}"`,
  formatters: {
    level: (label) => ({ severity: SEVERITY[label] ?? "DEFAULT" }),
    bindings: (b) => ({ service: b.service }), // keeps service, drops pid/hostname
  },
}, pino.destination(2));                       // fd 2, not the default

log.warn({ trace_id: "abc123", verb: "send", error: "NoSuchAgent" }, "send failed");
```

```json
{"severity":"WARNING","time":"2026-08-28T09:28:30.638Z","service":"labkit",
 "trace_id":"abc123","verb":"send","error":"NoSuchAgent","message":"send failed"}
```

`level: (label) => ({ severity: label.toUpperCase() })` emits `WARN`, which is
not a Cloud Logging severity and is silently read as `DEFAULT`.
`bindings: () => ({})`, the usual recipe for dropping `pid` and `hostname`,
also drops `service`. The envelope carries `pid` and `ppid` too; add them to
`base`.

**The fifth override is the destination, and it corrupts rather than
misleads.** pino writes to stdout. Where stdout is a protocol channel -- an MCP
server speaking over stdio -- a logger imported anywhere that server reaches
interleaves log lines into JSON-RPC. Both are JSON, so the client sees
plausible-looking corruption rather than a clean parse error. Measured in
LabKit (pino 10.3.1, bun 1.4.0): without a destination the record is on
STDOUT; with `pino.destination(2)` STDOUT is empty and the record is on
STDERR. Cloud Run ingests fd 2 identically: severity comes from the payload,
not the stream, so stderr is the portable choice.

**Adopting a logger retires whatever gate was protecting stdout.** Both
siblings grep for `console.log(` and `process.stdout.write(`; a logger call is
neither, and the check goes on reporting

```
OK: nothing under src/ writes to stdout except the CLI.
```

over a file whose whole purpose is writing to stdout. The destination override
and a gate that also matches logger call sites belong in the commit that adopts
the logger.

## Not covered

**Where a trace id is born.** An inbound HTTP request has one (Cloud Run
provides it), a bus message has one, and an **agent invocation** has none.
A session id survives `/compact` and is too coarse: one session carries twenty
merged pull requests, so joining on it joins everything to everything. The unit
has to be minted by whatever starts a piece of work rather than read off the
harness, and what that unit is remains open.

**Retention and rotation.** Nothing here rotates or deletes; that is the
operator's call.
