# infra/ci

The build pipeline for this repo. One terraform stack among possibly several —
see `../README.md` for the layout; **`agent-bus` needs none of it to run.**

Run terraform from this directory: state is local to it.

What is here:

| trigger | runs | identity | can it publish? |
|---|---|---|---|
| `publish-on-tag` | `cloudbuild.yaml` on `^v.*` | `ci-runner` | **yes** — mints a PyPI token |
| `test-on-pr` | `cloudbuild.test.yaml` on PRs to main | `ci-test` | no — log writer only |
| `e2e-manual` | `cloudbuild.e2e.yaml`, on demand | `ci-e2e` | no — reads API keys only |

Three triggers, three service accounts, no shared privilege. The split matters:
a PR build runs the contributor's own build config and Dockerfile, so it must
never hold the identity that can publish to PyPI.

Also here: Secret Manager containers for the three provider API keys the e2e
tiers need. Only the containers — versions are added by hand, never through
terraform, because a `secret_version` resource takes the value as an argument
and writes it to state in plaintext.

Applied by hand by the package author. `variables.tf` is committed; the two
variables without defaults come from the environment (see below).

## Running terraform here

Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in the two values
without defaults. Terraform reads it automatically — no exports, no `-var`, no
prompts. It is gitignored, because it holds the billing account id.

The values match the `GOOGLE_CLOUD_*` entries in the repo root `.env`:

| tfvars | .env |
|---|---|
| `billing_account_id` | `GOOGLE_CLOUD_BILLING_ACCOUNT` |
| `project_number` | `GOOGLE_CLOUD_PROJECT_NUMBER` |
| `project_id` | `GOOGLE_CLOUD_PROJECT` |

`TF_VAR_<name>` environment variables work too, but they must be exported in
every new shell — which is how an apply ends up prompting for a billing account
halfway through.

## Populating the e2e secrets

Terraform creates the secret *containers* and never the values, so a fresh apply
leaves three secrets with **zero versions** and `e2e-manual` fails on
`versions/latest`. Add them once:

```sh
set -a; . ../.env; set +a
printf %s "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-
printf %s "$OPENAI_API_KEY"    | gcloud secrets versions add openai-api-key    --data-file=-
printf %s "$XAI_API_KEY"       | gcloud secrets versions add xai-api-key       --data-file=-
```

`printf %s`, not `echo`: a trailing newline becomes part of the secret, and the
key then fails authentication in a way that looks like a bad key rather than a
bad upload.

Check what is there without revealing it:

```sh
gcloud secrets versions list anthropic-api-key
```

## Before the next apply: import the project

`google_project.build_project` is declared in `project.tf` but **is not in
state**, while the project plainly exists — builds run in it. A plan therefore
says `will be created`, and an apply would try to create a project whose id is
already taken and fail on that resource.

It was created by hand and never imported. `project.tf` now carries an **import
block**, which is the declarative form and the thing the docs bury under the old
CLI `terraform import` workflow:

```hcl
import {
  to = google_project.build_project   # the address in this config
  id = "agent-bus-build"              # what GCP calls it
}
```

Run `terraform apply` and Terraform adopts the existing project. Delete the
block afterwards; it is a one-time adoption.

Two things that are not obvious:

- **The `id` is provider-specific.** For `google_project` it is the bare project
  id, not `projects/agent-bus-build`.
- **Import creates nothing, but it will still show an update** if the config
  disagrees with reality. Here it did: `name` was `"agent-bus cloud-build
  project"` while the live project is `"agent-bus-build"`, so adoption wanted to
  rename it. The config now matches reality, and the plan is clean:

```
Plan: 1 to import, 13 to add, 0 to change, 0 to destroy.
```

Everything else was already tracked — the publish trigger, the ci-runner service
account and its IAM bindings.
