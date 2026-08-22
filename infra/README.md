# infra

Terraform for the maintainer's own build setup: a GCP project, a CI runner
service account, and a Cloud Build trigger (`publish-on-tag`) that runs
`cloudbuild.yaml` on tags matching `^v.*` to publish `agent-bus-team` to PyPI.

Applied by hand, once, by the package author. It is not live shared
infrastructure, not part of the published package, and nothing in `src/` or the
plugin depends on it. `variables.tf` is local and not committed.

**Agents: skip this directory.** It is documentation of an existing setup, not a
task surface. Do not review, refactor, or "fix" it unless asked directly.
