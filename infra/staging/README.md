# infra/staging

A second Cloud Run service in the **same project** as production, with its own
Firestore database, its own service account and its own signing key.

Terraform runs **from this directory** — state is local to it, and separate
from `infra/cloud`. Nothing here touches a resource `infra/cloud` owns.

## Why not a separate project

Because there is not one available: the billing account is at its quota of five
projects, and freeing a slot means unlinking something real.

That turns out to be the right answer anyway. A project boundary buys isolation
from a compromise of the project itself, which is not the threat here — the
threat is **a staging deploy writing to production's records**, and a named
Firestore database stops that for nothing.

## What isolates it, and what does not

| | |
|---|---|
| **Firestore database** | its own, named `staging`. Production keeps `(default)` |
| **signing key** | its own. **A staging token cannot be presented to production** |
| **service account** | its own, with `datastore.user` scoped to this database |
| **consent passphrase** | its own |
| project, APIs, Artifact Registry | **shared with production** |
| hostname | none — staging is reached at its `run.app` URL |

The signing key is the one that matters. Everything else is tidiness; that one
is the reason a mistake here cannot reach a connector there.

**No domain mapping, deliberately.** A hostname would put staging in
certificate transparency logs alongside production, and the `run.app` URL is
enough for something nothing is meant to find.

## Deploy

**Run terraform from the main checkout, not a worktree.** State is local and
gitignored, so it lives in the main checkout's `infra/staging/`. A worktree has its
own empty one, and an apply there would see no state and try to create
everything from scratch.

```sh
# once: the secret containers exist with zero versions
terraform apply -target=google_secret_manager_secret.staging
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add staging-signing-key --data-file=- --project agent-bus-cloud
printf %s 'a staging passphrase' \
  | gcloud secrets versions add staging-consent-passphrase --data-file=- --project agent-bus-cloud
# the secret GitHub signs with. Needed before the apply that mounts it: a
# container cannot mount a secret with no versions.
#
# Not thrown away like the two above -- the same string also goes into
# GitHub's webhook settings, or nothing verifies. `openssl rand -hex 32 |
# gcloud secrets versions add ...` alone -- the shape both secrets above use --
# generates the value inside the pipe and never shows it, so into a variable
# first, where there is something to paste into GitHub.
WEBHOOK_SECRET=$(openssl rand -hex 32)
printf '%s\n' "$WEBHOOK_SECRET"   # copy this into GitHub now, before it scrolls away
printf %s "$WEBHOOK_SECRET" \
  | gcloud secrets versions add staging-webhook-github-secret --data-file=- --project agent-bus-cloud
unset WEBHOOK_SECRET

terraform apply
terraform output service_url
```

The image comes from the **same Artifact Registry repository production uses**,
because staging exists to run the artefact that is about to be promoted. Point
it at a tag with `image` in `terraform.tfvars`; the default is Google's hello
container so a first apply completes before anything is built.

## How a build gets here

A `cloud-v*` tag runs `cloudbuild.deploy.yaml`: the same gate every other path
runs, then build, push, and `gcloud run services update` on this service.

```sh
git tag -a cloud-v0.2.1 -m "..." && git push origin cloud-v0.2.1
```

`docs/releasing.md` has the whole cycle: the preflight that says whether a tag
breaks something installed, and the postflight that asks the running server
which build it is serving.

Its own tag namespace, not `v*`. The package and the server have no reason to
ship together — coupling them means a docs-only release redeploying an
internet-facing OAuth server, and a server fix waiting on a package it did not
touch.

**CI owns the image on this service; terraform owns everything else.** There is
a `lifecycle { ignore_changes }` on the image for exactly that reason, because
CI cannot run terraform at all: the state is local to a laptop and gitignored,
deliberately. Without it the next `terraform apply` would silently revert
staging to whatever `var.image` said, undoing a deploy nobody remembered was
out of band.

**Production is not deployed this way.** Its image lives in
`infra/cloud/terraform.tfvars` and is applied by hand, so a promotion is a
decision and terraform can always say what is running.

## What it is for, and what it is not

**A mirror of production, for reproducing something without touching real
mailboxes.** Not a gate that changes pass through on the way to production —
they do not. Production is deployed straight from a tag and an apply, and that
is deliberate: one Google Cloud environment per customer, and the customer here
is the person writing it.

So the question staging answers is not *"is this safe to ship"*. It is *"what
is actually happening"*, asked somewhere you can insert, read and ack messages
freely without those messages being someone's real mail — a desktop peer's
inbox is a person's inbox, and a debugging session that acks half of it has
destroyed something.

It is also where a revision meets real Cloud Run, real Firestore and real
OAuth: the container entrypoint, the `X-Cloud-Trace-Context` header, IAM, cold
start. Those only break in the real environment.

**Not for:** ordinary development. The cloud test suite runs against a
Firestore emulator and the container runs locally against it; that covers
almost everything and costs nothing.

## Reaching it from a bridge

A cloud service is only half of it. To put a message through staging you need
a bridge pointed there, and that takes two things:

```sh
AGENT_BUS_CLOUD_TOKEN='<a token minted by staging>' \
  agent-bridge start --kind desktop --name claude-staging
```

**A distinct `--name`, not optional.** There is one bridge per address, and
`agent-bridge` refuses to start a second one for an address something already
holds — so a staging bridge calling itself `desktop:claude` would be refused by
the production one already running. Two bridges claiming one address would
otherwise race for the same queue and split delivery between them.

**`AGENT_BUS_CLOUD_TOKEN`, not the Keychain.** The URL comes out of the token's
own `iss` claim, and the Keychain holds exactly one item — so without the
environment variable every bridge on the machine resolves the same credential
and therefore the same deployment. See `docs/running-the-bridge.md`.

**Minting that token** is "Mint a bridge token" in `infra/cloud/README.md`,
with two substitutions: `--secret=staging-signing-key`, and the `issuer`
argument is staging's own Cloud Run URL — `terraform output service_url` here,
never `AGENT_BUS_CLOUD_ISSUER`. That variable is deliberately the unresolvable
`https://agent-bus-staging-placeholder.invalid` (see "Why not a separate
project" above); a token minted with it as `iss` would try to connect there
and fail DNS resolution before ever reaching staging.

## Debugging a live delivery

What this session actually did, in the order that worked, the first time
someone needed to find out why nothing was arriving.

**1. Read what the ingress actually recorded**, before assuming anything about
what it did not:

```sh
gcloud logging read 'resource.labels.configuration_name="agent-bus-staging"
  AND httpRequest.requestUrl:"/webhook"' \
  --project agent-bus-cloud --freshness=2h \
  --format='value(timestamp,httpRequest.status,httpRequest.requestUrl)'
```

Cloud Run's own `httpRequest.requestUrl` is worth checking before the app
logs: it is what proves a delivery actually reached the service at all, and it
is where a wrong Payload URL in the GitHub webhook settings shows up as a
string of 404s to a path the app never had a route for. `cloud/README.md`'s
"Reading the logs" has the `jsonPayload` query shapes for everything past
that — `verb`, `status`, `trace_id`.

**2. Mint a token for the address you want to test as**, per the section
above. `--kind webhook --name github` if the question is about consumption
rather than the ingress.

**3. `AF_UNIX` caps a socket path near 104 bytes.** `agent-bridge start`
publishes a listener under `AGENT_BUS_SOCK_DIR`, and a path built from a long
scratch or temp directory routinely blows past that — `bind()` fails on the
listener's background thread, the bridge prints nothing about it, and the run
looks like it started clean while silently unable to receive local mail.
`tests/conftest.py`'s `short_sock_dir` fixture exists for the identical
reason. A short one, made by hand:

```sh
mkdir -p /tmp/ab-test/s
AGENT_BUS_SOCK_DIR=/tmp/ab-test/s AGENT_BUS_CLOUD_TOKEN="$TOKEN" \
  agent-bridge start --kind webhook --name github
```

Omit `AGENT_BUS_HOME`/`AGENT_BUS_SESSIONS_DIR` to run on the machine's real
local bus rather than an isolated one — the right choice when the point is
reaching a real local peer (a live Claude Code session, say) rather than
proving the wire in isolation. Point them at scratch directories instead for
the latter.

**4. `pull` is non-destructive; only `ack` consumes.** Calling the `/bridge`
API's `pull` op directly (or letting a running bridge do it) is safe to repeat
while investigating — it never marks anything read. A running `agent-bridge
start`, though, *does* ack automatically once it has looked at a message, so
leaving one running drains the real queue: fine once you are done deciding
what is in it, not fine while you still want to look.

**5. Read Firestore directly when you need the raw payload**, past what
`pull` will show you. The path is `messages/<queue>/items/<message-id>` in the
`staging` database, `<queue>` being `webhook:<name>:outbox`:

```sh
uv run --with google-cloud-firestore python3 -c '
from google.cloud import firestore
db = firestore.Client(project="agent-bus-cloud", database="staging")
docs = db.collection("messages").document("webhook:github:outbox") \
         .collection("items").stream()
for d in docs:
    v = d.to_dict()
    print(d.id, v.get("summary"), len(v.get("text") or ""), "bytes")
'
```

**The 1-hour message TTL and Firestore's own TTL sweep are two different
clocks.** `pull` stops returning a message once the *application* TTL passes
— the same 3600 seconds every queue in this store uses — but the document
itself is not necessarily gone: Firestore's own background purge of `expireAt`
fields can lag hours behind. So a raw read can still find something `pull`
already refuses, for a while, but "for a while" is not a guarantee — capture
anything worth keeping (as a fixture, say — `cloud/tests/fixtures/`) promptly
rather than counting on the gap. GitHub keeps its own delivery history
independently of any of this, and any recent delivery can be redelivered from
its own UI on demand regardless of what has expired on this end.

Both bridges write to the same `agent-bridge.jsonl`. That is fine and worth
knowing: the `address` field is what tells them apart, and the `cloud endpoint`
record each writes at startup names which deployment it came up against.
