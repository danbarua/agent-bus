# infra

One directory per terraform stack. Each is independent — its own state, its own
`terraform.tfvars`, and potentially its own GCP project.

| stack | what it is |
|---|---|
| `ci/` | the build pipeline for this repo: three Cloud Build triggers, three service accounts, Secret Manager containers |

**`agent-bus` needs none of this to run.** Nothing in `src/`, nothing in the
published package, and nothing a user installs touches it. It is checked in so
the patterns can be reused elsewhere.

That is the only reason this README exists. Agents kept reading `infra/` as
cloud infrastructure `agent-bus` depends on and trying to make the library
"work" with it. It is the maintainer's own plumbing. Working on it deliberately
is fine.

## Adding a stack

Make a sibling directory. `.gitignore` already covers it: the patterns are
`infra/**/`, so state, `*.tfvars` and the provider cache are ignored in any
subdirectory from the moment it exists — and `.terraform.lock.hcl` stays
tracked, which is what pins provider versions.

Run terraform from inside the stack directory; state is local to it.
