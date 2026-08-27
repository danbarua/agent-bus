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
| hostname | `agent-bus.framesift.ai` — the OAuth issuer, so **it must not move** |
| region | `us-central1` — one of the ten that support domain mappings |
| spend cap | `max_instances`, default 4. Cloud Run scales to zero between messages |

## Before the first apply

**1. `framesift.ai` must be verified in Search Console**, under the account
running the apply. Not the subdomain — the base domain. Domain-mapping creation
fails without it, and it fails at *apply*, not at plan.

```sh
gcloud domains list-user-verified --project agent-bus-cloud   # after the run API exists
```

If it is not listed: https://search.google.com/search-console — add
`framesift.ai` as a Domain property and follow the TXT record it asks for.

**2. DNS is already correct and needs nothing.** `agent-bus.framesift.ai`
CNAMEs to `ghs.googlehosted.com`, which *is* the record a subdomain mapping
wants. Confirm rather than change:

```sh
dig +short agent-bus.framesift.ai CNAME     # ghs.googlehosted.com.
```

HTTPS fails today only because the mapping resource does not exist yet.

**3. Copy `terraform.tfvars.example` to `terraform.tfvars`** and fill in the
billing account id (`GOOGLE_CLOUD_BILLING_ACCOUNT` in the repo root `.env`).

## Apply, in two passes

Cloud Run cannot deploy an image that does not exist, and Artifact Registry
does not exist until this stack is applied. So the first pass runs Google's
hello container — which is not a placeholder for its own sake: it is what
creates the domain mapping and starts its TLS certificate provisioning, by far
the slowest step.

```sh
# pass 1 — project, APIs, Firestore, registry, secrets, a hello service
terraform apply

# the secrets. Containers exist with ZERO versions; the service will not start
# until both have one.
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add cloud-signing-key --data-file=- --project agent-bus-cloud
printf %s 'a passphrase you can say out loud' \
  | gcloud secrets versions add cloud-consent-passphrase --data-file=- --project agent-bus-cloud

# the real image, built in the project that runs it
gcloud builds submit cloud/ --project agent-bus-cloud \
  --tag "$(terraform output -raw image_repository)/server:$(date +%Y-%m-%d)"

# pass 2 — point the service at it
#   image = "us-central1-docker.pkg.dev/agent-bus-cloud/cloud/server:2026-08-27"
terraform apply
```

`printf %s`, never `echo`. A trailing newline becomes part of the secret, and
for the signing key that means every token verifies against a different key
than the one that signed it — which looks exactly like a client bug.

## Then check it works

```sh
curl -s https://agent-bus.framesift.ai/health
curl -s https://agent-bus.framesift.ai/.well-known/oauth-authorization-server | jq .issuer
```

The certificate takes up to about 20 minutes after the mapping is created. Until
then the `run.app` URL from `terraform output service_url` works and the custom
hostname does not.

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

## Recipes

```sh
# what the mapping actually wants in DNS, read from the resource
terraform output dns_records

# roll back to the previous image
#   edit `image` in terraform.tfvars to the older tag, then:
terraform apply

# rotate the signing key. Every existing token stops verifying, including the
# bridge's -- mint it a new one afterwards.
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add cloud-signing-key --data-file=- --project agent-bus-cloud
gcloud run services update agent-bus --region us-central1 --project agent-bus-cloud

# what is it costing
gcloud billing accounts list
```

## What is not here

**No CD trigger.** Deploys are `gcloud builds submit` plus an apply, by hand.
A push-to-deploy trigger on an internet-facing OAuth server is a thing to add
deliberately, with a reviewer, not as a side effect of standing it up.

**No cross-project IAM.** The image is built in `agent-bus-cloud` and pulled in
`agent-bus-cloud`, so the CI project's service accounts need no access here at
all. That was a choice: the alternative — build in CI, grant it write on this
registry — hands an identity in the project that can publish to PyPI a foothold
in the project that faces the internet.
