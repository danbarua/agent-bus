# Structured logging across three projects

A field contract for agent-bus, labkit and exo-ledger. It lets logs from all
three join up on one machine and in Cloud Logging.

**Status: adopted here.** `src/agent_bus/log.py` and `cloud/logs.py` are the
two reference implementations. They share no code. One lives in a package
that promises `dependencies = []`.

The other is a separate deployable. Two thirty-line formatters that agree on
field names implement this contract. No library is needed here.

`trace_id` is the message id on both sides of the boundary between the
bridge and the cloud. This contract covers that boundary.

This guarantee holds for the durable copy a message keeps as it crosses that
boundary. It does not hold inside the UDS wire protocol. An inbound frame's
own `msg_id` builds the delivery receipt only. It is never passed as the
stored message's id.

An outbound send mints one id for the wire frame. It mints a separate id for
the durable copy that `_sent()` returns to the caller. Two different,
unrelated ids exist there. See `docs/UDS-protocol.md`. `trace_id` does not
span them.

One query needs one log store. Shipping the bridge's logs upward would add a
failure mode at the moment logging matters most. This contract does not ship
the bridge's logs upward. The target is one identifier and one query
expression, in two places:

```sh
grep '"trace_id":"<id>"' ~/.local/state/agent-bus/agent-bus.jsonl
gcloud logging read 'jsonPayload.trace_id="<id>"' --project <project>
```

A message outlives the HTTP request that carried one leg of it. The message
id is the outer identifier. The Cloud Run request trace is a span within it.
Both are emitted. Neither replaces the other.

## Where it goes

    $XDG_STATE_HOME/<service>/<service>.jsonl     ~/.local/state when unset

One file per service, in the location the XDG state directory convention
already uses.

Human-readable output is a separate stream. It goes wherever the OS puts a
service's stdout, for example `~/Library/Logs/<service>/` under launchd.
Query the JSONL file. Read it during an incident.

## Correlation id agreement

Agree on the correlation id before you agree on anything else in the schema.

Three projects that share only `trace_id` are more debuggable than three
that share a perfect schema. A perfect schema does not help if each project
mints its own ids.

For a new surface, decide first where the id comes from and what carries it
across the boundary.

## The contract

Write one JSON object per line, in JSONL format. Use one file per service,
never a directory. A directory shards logs by pid or by day. Sharding
removes the single stream to read. It also removes ordering within the
service.

The three local projects write their own files. `service` records which
project emitted each line, once files are merged. Ordering across files
uses `time`, not position within one file.

Other fields go in as top-level, unnested fields, so `jq` stays cheap:

```json
{"time":"2026-08-28T09:19:02Z","severity":"WARNING","message":"send",
 "service":"agent-bus","trace_id":"cloud-abc123",
 "verb":"send","ok":false,"args":{"to":"ghost","text_len":1},
 "error":"no such agent: ghost"}
```

| field | | |
|---|---|---|
| `time` | required | ISO 8601, UTC, `2026-08-28T09:19:02Z` |
| `severity` | required | Use the key name `severity`, with one of `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL`. These are [Cloud Logging's values](https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry#logseverity). Cloud Logging has no `TRACE` or `WARN` value |
| `message` | required | One line. Put values in separate fields, not in the message text. When fields fully describe a record, `message` holds the verb, as in `{"message":"send","verb":"send"}` |
| `service` | required | which project emitted it |
| `trace_id` | when there is one | the correlation id |
| `span_id` | optional | |
| `verb` | when a caller asked for something | the client's intent: a CLI verb (`send`), a bridge op (`pull`), a JSON-RPC method (`tools/call`). One name serves one concept, across both services |
| `version` | cloud | the build that wrote the line. This lets you pin a regression found in the logs to a release |

### Field name convention

The field names follow [OpenTelemetry semantic
conventions](https://opentelemetry.io/docs/specs/semconv/general/logs/)
where a convention exists. OpenTelemetry is the only cross-language
vocabulary with real momentum. Adopting only the names has no cost. The
data already fits an SDK, if this project adopts one later.

This contract does not adopt the OpenTelemetry SDK. Auto-instrumentation
patches known libraries. It does not shim what this project runs. Manual
spans would give up the dependency-free property of this design. Revisit
the SDK question if a latency waterfall across services becomes more
valuable than causality. It has not become more valuable yet.

## Cloud Logging severity and trace fields

Cloud Logging reads the `severity` field. It ignores `level`. A line with
`level: 30` shows as INFO, regardless of intended severity. `pino` emits
`level` by default. Override it to `severity`.

Cloud Logging nests logs using its own key. It does not use `trace_id` for
this. Use this key to fold app logs under the request that produced them:

```json
"logging.googleapis.com/trace": "projects/<project-id>/traces/<trace-id>"
```

Emit both fields: `trace_id` for other consumers, and the qualified form
for Cloud Logging. On Cloud Run, the trace arrives in the
`X-Cloud-Trace-Context` header (`TRACE_ID/SPAN_ID;o=1`). The project id
must come from configuration. The header does not carry it.
`cloud/logs.py::trace_field` implements this in twelve lines.

## Levels

Four levels exist. Every one must have call sites. An advertised level with
no call sites is worse than a missing one. You turn it on, see the same
records, and conclude the thing you were hunting did not happen. agent-bus
advertised DEBUG that way for months.

| | |
|---|---|
| default | a failure, with its error. This level stays active regardless of the configured level, so a failure always reaches it |
| `INFO` | every call: what, to whom, how long, and did it work |
| `TRACE` | the firehose, one line per frame, when the wire itself is in question. Cloud Logging has no TRACE severity. TRACE-level records carry `DEBUG` severity instead. No other level emits `DEBUG` severity |
| off | No records emitted |

`AGENT_BUS_LOG_LEVEL` selects one level. `off`, `none`, `silent`, `quiet`,
`no` and `0` all mean the last row. Unset means the first row. A failure
still has to reach someone.

Levels and severities are different axes. The left column above selects
what is emitted. `severity` is what a record carries, using Cloud Logging's
permitted values. The two vocabularies collide on four words with different
meanings. The level `TRACE` emits records with `DEBUG` severity. The level
`default` differs from the severity value `DEFAULT`.

Only TRACE may record message content. Everywhere else, a body is measured,
never copied. A log that copies message text is a second inbox with a
different lifetime and no TTL. You must select TRACE. It does not turn on
by itself. Turn off TRACE when not diagnosing an issue.

TRACE truncates. A string field is capped at 8 KB. The untruncated length
is emitted beside it as `<field>_len`. The record states what it left out.

agent-bus caps a message at 32,768 characters. A `write()` call at that
size can split. The record's bytes remain in the file, but `jq` fails to
parse past the split point.

## Per language

Do not standardise on a library across languages. Standardise on the keys,
and use each ecosystem's idiomatic tool.

Python: use stdlib `logging` with a `Formatter` subclass. This takes thirty
lines and no dependency. Two working copies exist in this repo. Use
`structlog` instead, if the project has no dependency promise to keep.

TypeScript: use `pino`. It is JSON-first and fast. A dependency is
acceptable in a bun single-file binary, which is already large.

`pino` needs five overrides. Its defaults look correct but are wrong for
this contract. Run this configuration and check its output. Do not write it
from memory.

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
}, pino.destination(2));                       // fd 2. NOT the default; see below

log.warn({ trace_id: "abc123", verb: "send", ok: false }, "send failed");
```

```json
{"severity":"WARNING","time":"2026-08-28T09:28:30.638Z","service":"labkit",
 "trace_id":"abc123","verb":"send","ok":false,"message":"send failed"}
```

The one-line version, `level: (label) => ({ severity: label.toUpperCase() })`,
emits `"severity":"WARN"`. Cloud Logging has no `WARN` severity. It silently
downgrades to `DEFAULT`. The usual recipe for dropping `pid` and `hostname`,
`bindings: () => ({})`, also drops `service`.

The fifth override sets the destination. `pino` writes to stdout by
default. An MCP server speaking over stdio uses stdout as a protocol
channel. A logger imported anywhere that server transitively reaches
interleaves log lines into JSON-RPC output. Both are JSON, so the client
receives corrupted data instead of a clean parse error. This is harder to
detect than a stray `console.log`, which projects already guard against.

Measured in LabKit (pino 10.3.1, bun 1.4.0): the configuration above,
without a destination, puts the record on STDOUT. With
`pino.destination(2)`, STDOUT stays empty and the record goes to STDERR.
This default looks correct. It is wrong for a project where stdout carries
a protocol.

Adopting a logger retires any gate that was protecting stdout, in every
project that had one. This consequence applies to any project with such a
gate, not only LabKit. The projects that most need this override have
gates that will not detect the problem. Both sibling projects grep for
`console.log(` and `process.stdout.write(`. A logger call matches neither
pattern. Measured in both: dropping a logger-shaped call under `src/`
leaves the check reporting

```
OK: nothing under src/ writes to stdout except the CLI.
exit=0
```

exo-ledger has no `pino` today. Adopting `pino` there erodes stdout
protection on the commit that adds it. `pino.destination(2)` and a gate
that also matches logger call sites belong in that same commit. A gate
written for the old shape of a symptom does not detect the new one.

## Gaps in this contract

**Trace id origin, per surface.** Three surfaces need a trace id origin.
An inbound HTTP request gets one from Cloud Run. A bus message carries one
end to end in agent-bus. An agent invocation has no id source yet.

Until a coding agent's work carries an id, a thought cannot be tracked
across all three projects. This is the open gap in the contract.

**Trace id scope.** A session id exists and survives `/compact`. It is too
coarse for this purpose: one session has put twenty merged pull requests
under a single id. Joining on a session id merges unrelated work together.
No harness exposes a unit finer than a session. The unit has to be minted
by whatever starts a piece of work. Naming that unit is the open question.

**Retention and rotation.** This contract leaves retention and rotation
unspecified. It does not rotate logs. It does not delete them. Rotation and
deletion are the operator's responsibility, on their machine.
