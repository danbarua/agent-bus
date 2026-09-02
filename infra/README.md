# infra

One directory per terraform stack. Each is independent — its own state, its own
`terraform.tfvars`, and potentially its own GCP project.

| stack | what it is |
|---|---|
| `ci/` | the build pipeline for this repo: Cloud Build triggers, service accounts, Secret Manager containers. Project `agent-bus-build` |
| `cloud/` | **production.** The public server behind the OAuth issuer hostname, its Firestore database, its domain mapping. Project `agent-bus-cloud`. Applied by hand — a promotion is a decision |
| `staging/` | a second Cloud Run service in the *same* project, with its own Firestore database and its own signing key. Deployed by CI on a `cloud-v*` tag |

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

`cloud/` and `staging/` share a project and are still separate stacks with
separate state: nothing in one touches a resource the other owns. What actually
isolates them is the **signing key**, not the directory — a staging token
cannot be presented to production. `infra/staging/README.md` has the table of
what is shared and what is not.

**Run terraform from the main checkout, not a worktree.** State is local and
gitignored, so it lives in the main checkout's `infra/<stack>/`. A worktree has
its own empty one, and an apply there would see no state and try to create
everything from scratch.
