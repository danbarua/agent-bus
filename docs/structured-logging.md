# Structured logging across three projects

A field contract for agent-bus, labkit and exo-ledger, so logs from all three
join up on one machine and in Cloud Logging.

**Status: adopted here, with one gap.** `src/agent_bus/log.py` and
`cloud/logs.py` are the two reference implementations, and they deliberately
share no code — one is in a package that promises `dependencies = []`, the
other is a separate deployable. Two thirty-line formatters agreeing on field
names is the whole mechanism. Nothing here needs a library.

`trace_id` **is the message id**, on both sides. It was neither, briefly: the
bus logged arguments and not results, so the id never reached a record at all,
and the cloud had the request trace and no message id. The doc claimed the gap
was "two queries"; it was zero, because there was nothing to join on.

One query would need one log store, and shipping the bridge's logs upward was
rejected on purpose — it adds a failure mode exactly when it is most needed.
So the target is **one identifier and one query expression, in two places**:

```sh
grep '"trace_id":"<id>"' ~/agent-bus.jsonl
gcloud logging read 'jsonPayload.trace_id="<id>"' --project <project>
```

A message outlives the HTTP request that carried one leg of it, so the message
id is the outer identifier and the Cloud Run request trace is a span within it.
Both are emitted; neither replaces the other.

## The rule that matters more than the schema

**Agree on the correlation id before agreeing on anything else.**

Three projects that share `trace_id` and nothing else are more debuggable than
three that share a perfect schema while each mints its own ids. That is not
theoretical: agent-bus had a correct log on both sides of a message and could
not answer *"where did this get to"*, because the cloud minted an id, the
bridge dropped it, and the local bus minted a second. One parameter fixed it,
and only then was the logging worth reading.

So the first question for any new surface is not *what do I log* but **where
does the id come from, and what carries it across the boundary**.

## The contract

One JSON object per line. JSONL, one file, never a directory — every record
says who emitted it, so a single file demultiplexes with `jq` *and keeps the
ordering between services*, which is the thing you need when A sent and B
never saw it.

| field | | |
|---|---|---|
| `time` | required | ISO 8601, UTC, `2026-08-28T09:19:02Z` |
| `severity` | required | `DEBUG` `INFO` `WARNING` `ERROR`. **This exact key**, and one of [Cloud Logging's values](https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry#logseverity) — there is no `TRACE` or `WARN` |
| `message` | required | one human-readable line. Not a template — the values go in fields |
| `service` | required | which project emitted it |
| `trace_id` | when there is one | the correlation id |
| `span_id` | optional | |

Everything else goes in as **top-level fields, unnested**, so `jq` stays cheap:

```json
{"time":"2026-08-28T09:19:02Z","severity":"WARNING","message":"send",
 "service":"agent-bus","trace_id":"cloud-abc123",
 "verb":"send","ok":false,"args":{"to":"ghost","text_len":1},
 "error":"no such agent: ghost"}
```

### Why these names

They are [OpenTelemetry semantic
conventions](https://opentelemetry.io/docs/specs/semconv/general/logs/) where
they exist, because that is the only genuinely cross-language vocabulary with
momentum — and it is **just names**. Adopting it costs nothing and means the
data already fits if an SDK is ever wanted.

We are not adopting the OTel SDK. Auto-instrumentation works by patching known
libraries and shims none of what we run; writing manual spans forfeits the
dependency-free property that made the idea attractive. Revisit it if a latency
waterfall across services is ever worth more than causality, which it is not
yet.

## Two things Cloud Logging will bite you on

**It reads `severity`, not `level`.** A line with `level: 30` is INFO forever,
however loudly it was logged. `pino` emits `level` by default — override it.

**It nests on its own key, not on `trace_id`.** To get app logs folded under
the request that produced them:

```json
"logging.googleapis.com/trace": "projects/<project-id>/traces/<trace-id>"
```

Emit **both** — `trace_id` for everyone else, the qualified form for GCP. On
Cloud Run the trace arrives as the `X-Cloud-Trace-Context` header
(`TRACE_ID/SPAN_ID;o=1`); the project id has to come from configuration,
because the header does not carry it. `cloud/logs.py::trace_field` is twelve
lines and does exactly this.

## Levels

Four, and **every one must have call sites**. An advertised level with nothing
on it is worse than a missing one: you turn it on, see the same records, and
conclude the thing you were hunting did not happen. agent-bus advertised DEBUG
that way for months.

| | |
|---|---|
| default | a failure, with its error. This is the level everything runs at, so it is the level a failure has to reach |
| `INFO` | every call: what, to whom, how long, and did it work |
| `TRACE` | the firehose — one line per frame, when the wire itself is in question. Cloud Logging has no TRACE severity, so anything shipped there maps to `DEBUG` |
| off | nothing |

**Only TRACE may record message content.** Everywhere else a body is measured
and never copied: a log that copies message text is a second inbox with a
different lifetime and no TTL. TRACE is the deliberate exception, it is never
selected by accident, and it should not be left on.

## Per language

Do **not** standardise on a library across languages. Standardise on the keys
and use each ecosystem's idiomatic tool.

**Python** — stdlib `logging` with a `Formatter` subclass. Thirty lines, no
dependency, and there are two working copies in this repo. `structlog` if you
want it and the project has no dependency promise to keep.

**TypeScript** — `pino`. JSON-first and fast, and a dependency is fine in a bun
single-file binary that is hefty anyway.

Pino needs **five** overrides, not one, and the defaults are wrong in ways that
look right. This config is run and its output checked, not written from memory:

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

The obvious one-line version — `level: (label) => ({ severity: label.toUpperCase() })`
— emits `"severity":"WARN"`, which **is not a Cloud Logging severity** and is
silently downgraded to DEFAULT. `bindings: () => ({})`, the usual recipe for
dropping `pid` and `hostname`, also drops `service`. Both were in the first
draft of this file and both were caught by running it.

**The fifth override is the destination, and it corrupts rather than misleads.**
pino writes to stdout. Where stdout is a *protocol* channel — an MCP server
speaking over stdio, most obviously — a logger imported anywhere that server
transitively reaches interleaves log lines into JSON-RPC. Both are JSON, so the
client gets plausible-looking corruption rather than a clean parse error, which
is worse than the stray `console.log` such projects usually guard against.

Measured in LabKit (pino 10.3.1, bun 1.4.0): the config above without a
destination puts the record on STDOUT; with `pino.destination(2)` STDOUT is
empty and the record is on STDERR. So `pino` is not drop-in for those projects,
and the reason belongs with the other four: a default that looks right.

Worth knowing even where stdout is free today, because adopting a logger can
retire the gate that was protecting it. LabKit's `check-stdout.sh` greps for
`console.log(` and `process.stdout.write(`; a logger call is neither, so the
check passes and goes on reporting OK. A gate written against the old shape of
a symptom does not announce that it has stopped covering the new one.

## What this does not cover

**Where a trace id is born, per surface.** Three places matter and only two are
solved: an inbound HTTP request (Cloud Run provides one), a bus message
(agent-bus now carries one end to end), and an **agent invocation** — which has
nothing. Until a coding agent's work carries an id, a thought cannot be
followed across all three projects, and that is the interesting gap rather than
any field name here.

**Retention and rotation.** Deliberately unspecified: `AGENT_BUS_LOG_FILE`
names a file when you want one, and nothing writes a file nobody asked for,
because a log file nobody asked for is a file nobody deletes.
