# infra

Terraform for the maintainer's own build setup. **`agent-bus` does not need any
of this to run** — nothing in `src/`, nothing in the published package, and
nothing a user installs touches it. It is checked in so the CI pattern can be
recreated in other projects.

That is the only reason this README exists. Agents kept reading `infra/` as
cloud infrastructure `agent-bus` depends on and trying to make the library
"work" with it. It is the maintainer's build plumbing, nothing more. Working on
it deliberately — as CI work — is fine.

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
