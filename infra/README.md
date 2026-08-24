# infra

Terraform for the maintainer's own build setup: a GCP project, a CI runner
service account, and a Cloud Build trigger (`publish-on-tag`) that runs
`cloudbuild.yaml` on tags matching `^v.*` to publish `agent-bus-team` to PyPI.

Applied by hand, once, by the package author. It is not live shared
infrastructure, not part of the published package, and nothing in `src/` or the
plugin depends on it. `variables.tf` is local and not committed.

**Agents: skip this directory.** It is documentation of an existing setup, not a
task surface. Do not review, refactor, or "fix" it unless asked directly.

## Running terraform here

The three variables without defaults are already in your environment as
`GOOGLE_CLOUD_*`. Terraform reads `TF_VAR_<name>`, so map them rather than
passing `-var` every time:

```sh
set -a; . ../.env; set +a
export TF_VAR_project_id="$GOOGLE_CLOUD_PROJECT"
export TF_VAR_project_number="$GOOGLE_CLOUD_PROJECT_NUMBER"
export TF_VAR_billing_account_id="$GOOGLE_CLOUD_BILLING_ACCOUNT"
terraform plan
```

`infra/*.tfvars` is gitignored if you prefer a file — it would hold the billing
account id, so it must never be committed.

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
