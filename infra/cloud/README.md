# infra/cloud

The public server. Terraform runs **from this directory** — state is local to
it, and separate from `infra/ci`.

Two projects, on purpose. `agent-bus-build` runs CI: it holds the identity that
can publish to PyPI and the provider API keys the e2e tier spends real money
with. This one is reachable from the internet by anyone who reads a certificate
transparency log. Nothing here should be able to reach anything there.

| | |
|---|---|
| project | `agent-bus-cloud` |
| hostname | `bus.example.com` — the OAuth issuer, so **it must not move** |
| region | `us-central1` — one of the ten that support domain mappings |
| spend cap | `max_instances`, default 4. Cloud Run scales to zero between messages |

## Before the first apply

**0. Run terraform from the main checkout, not a worktree.** State is local and
gitignored, so it lives in the main checkout's `infra/cloud/`. A worktree has
its own empty one, and an apply there would see no state and try to create
everything from scratch — the project, the database, the service. It would fail
on the project, because `deletion_policy = PREVENT` and the id is taken, but
only after attempting the rest.

Same rule for `infra/staging` and `infra/ci`.

**1. `example.com` must be verified in Search Console**, under the account
running the apply. Not the subdomain — the base domain. Domain-mapping creation
fails without it, and it fails at *apply*, not at plan.

```sh
gcloud domains list-user-verified --project agent-bus-cloud   # after the run API exists
```

If it is not listed: https://search.google.com/search-console — add
`example.com` as a Domain property and follow the TXT record it asks for.

**2. DNS is already correct and needs nothing.** `bus.example.com`
CNAMEs to `ghs.googlehosted.com`, which *is* the record a subdomain mapping
wants. Confirm rather than change:

```sh
dig +short bus.example.com CNAME     # ghs.googlehosted.com.
```

HTTPS fails today only because the mapping resource does not exist yet.

**3. Copy `terraform.tfvars.example` to `terraform.tfvars`** and fill in the
billing account id (`GOOGLE_CLOUD_BILLING_ACCOUNT` in the repo root `.env`).

## Apply, in three passes

Cloud Run cannot deploy an image that does not exist, and Artifact Registry
does not exist until this stack is applied — so the service comes up on
Google's hello container first and is pointed at the real one afterwards.

`image` has **no default**: forgetting it in a `terraform.tfvars` would
otherwise replace a live service with that hello container, silently. So the
bootstrap names it explicitly, which is the only place it should ever appear.

Three passes rather than two, because secrets have to hold a value before
anything can mount them.

```sh
# pass 0 — the project, the APIs and the three secret CONTAINERS, and nothing
# that consumes them. `-target` pulls in everything they depend on.
#
# This step exists because of the order Cloud Run insists on: the service
# template refers to `latest` of every secret, and a container with zero
# versions has no `latest`. Applying everything at once fails the revision --
# and therefore the whole apply, halfway through, on your first run.
terraform apply -target=google_secret_manager_secret.cloud

# the values. `printf %s`, never `echo` -- see below.
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add cloud-signing-key --data-file=- --project agent-bus-cloud
printf %s 'a passphrase you can say out loud' \
  | gcloud secrets versions add cloud-consent-passphrase --data-file=- --project agent-bus-cloud
# the secret GitHub signs with. One peer, one secret -- a second source is
# another secret and another mount, both terraform.
#
# Unlike the two above, this one is not thrown away: the same string also
# goes into GitHub's own webhook settings (Repository -> Settings -> Webhooks
# -> Secret), or nothing verifies. `openssl rand -hex 32 | gcloud secrets
# versions add ...` -- the shape both secrets above use -- generates the value
# *inside the pipe* and never shows it, which is fine for a secret nothing
# else needs to know and unusable for one two systems must agree on. Generate
# it first, into a variable, so there is something to paste into GitHub after:
WEBHOOK_SECRET=$(openssl rand -hex 32)
printf '%s\n' "$WEBHOOK_SECRET"   # copy this into GitHub now, before it scrolls away
printf %s "$WEBHOOK_SECRET" \
  | gcloud secrets versions add cloud-webhook-github-secret --data-file=- --project agent-bus-cloud
unset WEBHOOK_SECRET

# pass 1 — Firestore, the registry, the domain mapping, and a hello service.
# The hello container is not a placeholder for its own sake: this is what
# creates the mapping and starts its TLS certificate provisioning, which is by
# far the slowest step and wants a head start.
terraform apply -var image=us-docker.pkg.dev/cloudrun/container/hello

# the real image, built in the project that runs it.
#
# `--build-arg VERSION` is what makes `/health` report something other than
# `0+unknown`. `gcloud builds submit --tag` cannot pass one, so a hand-built
# image is anonymous by construction -- fine for a bootstrap, and the reason
# the `cloud-v*` tag path exists for everything after it.
TAG="$(terraform output -raw image_repository)/server:$(date +%Y-%m-%d)"
docker build --build-arg "VERSION=$(basename "$TAG")" -t "$TAG" ../../cloud/
docker push "$TAG"

# pass 2 — point the service at it
#   image = "us-central1-docker.pkg.dev/agent-bus-cloud/cloud/server:2026-08-27"
terraform apply
```

`printf %s`, never `echo`. A trailing newline becomes part of the secret, and
for the signing key that means every token verifies against a different key
than the one that signed it — which looks exactly like a client bug. For the
webhook secret it means every delivery fails its HMAC, which looks exactly like
a misconfigured hook.

### The GitHub side

The same value goes in the hook itself. Repository → Settings → Webhooks → Add:

| | |
|---|---|
| Payload URL | `https://<host>/webhook/github` |
| Content type | **`application/json`** |
| Secret | the value you just added |
| Events | `pull_request`, `issue_comment` |

**Content type is not a preference.** `application/x-www-form-urlencoded` sends
`payload=<urlencoded json>`, which verifies its HMAC perfectly and is then
undecodable by the bridge, in another process, minutes later. The endpoint
refuses it with a 415 naming the setting, and the `ping` GitHub sends when you
save the hook hits that check — so a wrong choice fails in front of you rather
than on the first real event.

The secret needs a version before the apply that mounts it, because a container
cannot mount one that has none -- the same ordering the other two have. Until a
hook exists the value is simply unused; deliveries to a name with no secret are
404s.


## Then check it works

```sh
curl -s https://bus.example.com/health
curl -s https://bus.example.com/.well-known/oauth-authorization-server | jq .issuer
```

The certificate takes up to about 20 minutes after the mapping is created. Until
then the `run.app` URL from `terraform output service_url` works and the custom
hostname does not.

## When an apply fails halfway

Terraform **taints** a resource whose create partially succeeded, and a tainted
resource is replaced on the next run — which `deletion_policy = "PREVENT"`
then refuses:

```
Error: Cannot destroy project as deletion_policy is set to PREVENT.
```

The project is fine; only the step after creating it failed. Clear the mark and
re-apply:

```sh
terraform untaint google_project.cloud
terraform apply
```

This is not hypothetical — the first apply of this stack hit it, because a
billing account has a **quota of five projects** and linking the sixth is
refused with `Cloud billing quota exceeded`. Unlink or delete a dormant
project, or use the quota-increase form the error links to.

## Things that will look wrong and are not

**The service is public (`allUsers` can invoke).** An MCP connector pings
`initialize` and `tools/list` *before* it ever attaches an `Authorization`
header. Gating at the network means no tool is visible at all, whether or not
auth works. Authorization is the server's job — discovery answers anonymously,
`tools/call` and `/bridge` do not. See `cloud/README.md`.

**Domain mappings are a Preview feature** and Google's own documentation
recommends a global external Application Load Balancer instead, citing latency.
That ALB is roughly $20/month before a single request, needs DNS repointed to an
A record, and replaces one resource with six. For one person's message bus the
mapping is the right trade. This paragraph is the escape hatch if it ever bites.

**No TTL index, deliberately.** `expireAt` is monotonically increasing, which is
the textbook Firestore write hotspot, and nothing queries by it — the server
filters expired documents in memory, because TTL is a collector and not a
filter. `index_config {}` is an empty block, not an omission.

**Three TTL policies, and `oauth_clients` is not one of them.** ChatGPT caches
its `client_id` and reuses it indefinitely, so expiring a registration orphans a
live connector. The authoritative list is the `Firestore` class docstring in
`cloud/store.py`; these two have to agree.

**A misconfigured revision fails to deploy rather than serving.** The server
refuses to start without a signing key, so the startup probe fails and the
previous revision keeps traffic. The failure that guards against is a container
answering `/health` perfectly while authenticating nobody.

## Deployed

Standing up `bus.example.com` took the three passes above plus two
detours, both now documented. Certificate issuance took about 50 minutes from
mapping creation, not the 20 the docs suggest — the `run.app` URL serves
throughout, so nothing is blocked on it.

## Shipping what is on main

The ordinary loop, once the stack exists. Not the three passes above -- those
are for standing it up. `docs/releasing.md` covers the tag namespaces and the
before/after checks; this section is the by-hand promotion those checks bracket.

```sh
# 1. terraform.tfvars is GITIGNORED, so a fresh checkout has none. Rebuild it:
#      billing_account_id  <- GOOGLE_CLOUD_BILLING_ACCOUNT in the repo root .env
#      hostname            <- required, no default, deliberately not in the repo
#      image               <- set in step 3
#      allowlist           <- redirect URI -> peer address, empty is valid
cp terraform.tfvars.example terraform.tfvars && $EDITOR terraform.tfvars

# 2. build and push, in the project that runs it. `--build-arg VERSION` is what
#    lets step 4 confirm the deploy on its own -- omit it and `/health` reports
#    `0+unknown`, which is honest and useless as a check. `gcloud builds
#    submit --tag` cannot pass a build arg, which is why this is docker.
TAG="$(terraform output -raw image_repository)/server:$(date +%Y-%m-%d)"
docker build --build-arg "VERSION=$(basename "$TAG")" -t "$TAG" ../../cloud/
docker push "$TAG"

# 3. point the service at it
sed -i '' "s|^image = .*|image = \"$TAG\"|" terraform.tfvars
terraform apply

# 4. confirm the revision actually took traffic. The version comes from inside
#    the image, so this is the check rather than a proxy for it.
curl -s https://<hostname>/health          # {"ok": true, "version": "<tag>"}
curl -s https://<hostname>/ -o /dev/null -w '%{http_code}\n'
```

**Step 4 is not ceremony.** A revision that fails its startup probe leaves the
*previous* one serving, and `terraform apply` reports success either way -- the
service exists and matches the config. `/health` answered 200 throughout a
period when the deployment was five merges behind, because the old revision was
still healthy.

A 200 is still not the check. **The `version` in that response is** -- it comes
from inside the image (`ARG VERSION`, baked at build time), so it cannot be the
old revision's answer. That only holds for an image built with the build arg:
one without reports `0+unknown`, and then you are back to needing something
else the new build has and the old one does not.

**Reuse a date tag and nothing happens.** Cloud Run compares image *references*,
not digests, so pushing over `server:2026-08-28` and re-applying is a no-op:
terraform sees no change. Append a suffix -- `-2`, `-3` -- or use the commit sha.

## Recipes

```sh
# what the mapping actually wants in DNS, read from the resource
terraform output dns_records

# what IS deployed right now, versus what is on main
gcloud run services describe agent-bus --region us-central1 \
  --project agent-bus-cloud --format='value(spec.template.spec.containers[0].image)'

# roll back to the previous image
#   edit `image` in terraform.tfvars to the older tag, then:
terraform apply

# rotate the signing key. Every existing token stops verifying, and so does
# every bridge's shared secret -- rebuild the credential below afterwards.
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add cloud-signing-key --data-file=- --project agent-bus-cloud
gcloud run services update agent-bus --region us-central1 --project agent-bus-cloud

# what is it costing
gcloud billing accounts list
```

### Make a bridge credential

There is no minting step: every bridge in this environment -- present and
future, whatever address it stands in for -- shares one static secret, the
signing key itself. Nothing is generated per address, so this is a one-time
step per environment, not something run again when a second bridge shows up.

```sh
# 1. the signing key, into a variable -- never a file, never the terminal
#    history. It is the one thing every bridge in this environment presents.
SIGNING_KEY=$(gcloud secrets versions access latest \
  --secret=cloud-signing-key --project agent-bus-cloud)

# 2. wrap it with the server it's for. Nothing here is signed or verified --
#    it's a label a bridge reads for itself, not a claim the server checks
#    (see `read_cloud_token()` in `src/agent_bridge/bridge.py`).
python3 -c '
import base64, json, sys
prefix = base64.urlsafe_b64encode(
    json.dumps({"iss": "https://bus.example.com"}).encode()  # see the note below
).decode().rstrip("=")
print(f"{prefix}.{sys.argv[1]}")
' "$SIGNING_KEY"
```

**The `iss` value is the URL the bridge will actually dial, not necessarily
`AGENT_BUS_CLOUD_ISSUER`.** `read_cloud_token()` takes the connection URL from
this prefix and never verifies it -- the server only ever checks the secret
half. So the two are free to differ, and on `infra/staging` they must: its
`AGENT_BUS_CLOUD_ISSUER` is the deliberately unresolvable
`https://agent-bus-staging-placeholder.invalid` (see `infra/staging/README.md`),
and a credential wrapped with *that* as `iss` would try to connect there and
fail DNS resolution. Pass the real, reachable URL instead -- `terraform output
service_url`, or the Cloud Run URL directly.

Put the result in the Keychain (`docs/running-the-bridge.md`) and every local
bridge kind or name uses it unchanged -- `desktop:claude`, `webhook:github`,
anything added later. `AGENT_BUS_CLOUD_TOKEN` is for pointing one specific
bridge at a *different environment* (production vs. staging), never for
giving a second address its own credential -- there is no such thing any
more, see "Reaching it from a bridge" in `infra/staging/README.md`.

## What is not here

**No CD trigger.** Deploys are `gcloud builds submit` plus an apply, by hand.
A push-to-deploy trigger on an internet-facing OAuth server is a thing to add
deliberately, with a reviewer, not as a side effect of standing it up.

**No cross-project IAM.** The image is built in `agent-bus-cloud` and pulled in
`agent-bus-cloud`, so the CI project's service accounts need no access here at
all. That was a choice: the alternative — build in CI, grant it write on this
registry — hands an identity in the project that can publish to PyPI a foothold
in the project that faces the internet.
