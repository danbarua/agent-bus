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

## What it is for, and what it is not

**For:** verifying a revision against real Cloud Run, real Firestore and real
OAuth before it takes production traffic — the handful of things that only
break in the real environment. The container entrypoint, the
`X-Cloud-Trace-Context` header, IAM, cold start.

**Not for:** ordinary development. The cloud test suite runs against a Firestore
emulator and the container runs locally against it; that covers almost
everything and costs nothing. Reach for staging when the question is *"will
this deploy work"*, not *"does this code work"*.
