# infra/ci

Recipes for the things you do here occasionally and have to look up.

Terraform runs **from this directory** — state is local to it. `agent-bus`
itself needs none of this to run.

| trigger | runs | on | identity |
|---|---|---|---|
| `test-on-pr` | `cloudbuild.test.yaml` | PRs to main | `ci-test` — log writer only |
| `build-images-on-pr` | `cloudbuild.image.yaml` | PRs touching the images | `ci-test` |
| `e2e-manual` | `cloudbuild.e2e.yaml` | on demand | `ci-e2e` — reads API keys |
| `publish-on-tag` | `cloudbuild.yaml` | tags `^v.*` | `ci-runner` — **can publish to PyPI** |

A PR build runs the contributor's own build config and Dockerfile, so it must
never hold the identity that can publish. That is the whole reason for four
service accounts.

## Run the spendy tests against a PR

They do not run on PRs. `e2e-manual` is pinned to `main`, so point it at the
branch:

```sh
gcloud builds triggers run e2e-manual \
  --region=us-central1 --project=agent-bus-build \
  --branch=my-feature-branch
```

**The build config still comes from `main`.** `git_file_source.revision` is
pinned there, so a change to `cloudbuild.e2e.yaml` on your branch is *not* used
— only the source it checks out changes. To test a change to the config itself,
merge it or edit the trigger.

Pin a harness version to reproduce a suspected regression:

```sh
gcloud builds triggers run e2e-manual --region=us-central1 \
  --project=agent-bus-build --branch=my-branch \
  --substitutions=_GROK_VERSION=1.0.4
```

## Replace an API key

Secret Manager holds the *containers*; the values are added by hand. Terraform
never sees them — a `secret_version` resource takes the value as an argument
and writes it to state in plaintext.

```sh
set -a; . ../.env; set +a
printf %s "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-
printf %s "$OPENAI_API_KEY"    | gcloud secrets versions add openai-api-key    --data-file=-
printf %s "$XAI_API_KEY"       | gcloud secrets versions add xai-api-key       --data-file=-
```

`printf %s`, never `echo`: a trailing newline becomes part of the secret, and
the key then fails authentication in a way that looks like a bad key rather
than a bad upload.

Adding a version supersedes the old one; nothing else is needed. Check what is
there without revealing it:

```sh
gcloud secrets versions list anthropic-api-key
```

A fresh `terraform apply` creates the containers with **zero versions**, and
`e2e-manual` then fails on `versions/latest`. Run the block above once after
one.

## Read a failing build

```sh
gh pr checks <pr>                                    # which one failed, and its URL
gcloud builds log <build-id> --region=us-central1 --project=agent-bus-build
```

## Run terraform here

**Run terraform from the main checkout, not a worktree.** State is local and
gitignored, so it lives in the main checkout's `infra/ci/`. A worktree has its
own empty one, and an apply there would see no state and try to create
everything from scratch.

Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in the two
values without defaults. Terraform reads it automatically — no exports, no
`-var`, no prompts. Gitignored, because it holds the billing account id.

| tfvars | root `.env` |
|---|---|
| `billing_account_id` | `GOOGLE_CLOUD_BILLING_ACCOUNT` |
| `project_number` | `GOOGLE_CLOUD_PROJECT_NUMBER` |
| `project_id` | `GOOGLE_CLOUD_PROJECT` |

`TF_VAR_<name>` works too, but must be exported in every new shell — which is
how an apply ends up prompting for a billing account halfway through.

### This stack now touches a second project

`deploy-cloud-on-tag` builds here and deploys into `agent-bus-cloud`, so three
resources reach across: two project-level grants on that project, and
`serviceAccountUser` on `agent-bus-staging-run@agent-bus-cloud`.

Two consequences for an apply, neither of which the plan explains when it
fails:

**`infra/staging` first.** That service account is created there. Applied in
the other order, this stack fails on a service account that does not exist yet.

**The identity running `terraform apply` here needs IAM admin on
`agent-bus-cloud`**, not only on the build project. Until this trigger existed,
that was never true.

The grants are project-level, which is wider than the deploy needs — see
[#122](https://github.com/danbarua/agent-bus/issues/122).
