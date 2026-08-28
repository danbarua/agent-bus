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

```sh
# once: the secret containers exist with zero versions
terraform apply -target=google_secret_manager_secret.staging
printf %s "$(openssl rand -hex 32)" \
  | gcloud secrets versions add staging-signing-key --data-file=- --project agent-bus-cloud
printf %s 'a staging passphrase' \
  | gcloud secrets versions add staging-consent-passphrase --data-file=- --project agent-bus-cloud

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

**For:** verifying a revision against real Cloud Run, real Firestore and real
OAuth before it takes production traffic — the handful of things that only
break in the real environment. The container entrypoint, the
`X-Cloud-Trace-Context` header, IAM, cold start.

**Not for:** ordinary development. The cloud test suite runs against a Firestore
emulator and the container runs locally against it; that covers almost
everything and costs nothing. Reach for staging when the question is *"will
this deploy work"*, not *"does this code work"*.
