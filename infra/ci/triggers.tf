locals {
  repo_uri = "https://github.com/${var.github_owner}/${var.github_repo}"

  # The tag runner. Named once because three cross-project grants below hand it
  # to a different project, and three literal spellings of one identity is how
  # a tightening silently misses one of them.
  ci_runner = "serviceAccount:${var.project_id}-ci-runner@${var.project_id}.iam.gserviceaccount.com"
}

# __generated__ by Terraform from "projects/mighty-colab/locations/global/triggers/6cce1bea-a0c0-4746-9935-ad6b048ccf90"
resource "google_cloudbuild_trigger" "publish_on_tag" {
  deletion_policy    = "PREVENT"
  description        = "Publish on matched tag"
  disabled           = false
  filename           = "cloudbuild.yaml"
  filter             = null
  ignored_files      = []
  include_build_logs = null
  included_files     = []
  location           = var.region
  name               = "publish-on-tag"
  project            = var.project_id
  service_account    = "projects/${var.project_id}/serviceAccounts/${var.project_id}-ci-runner@${var.project_id}.iam.gserviceaccount.com"
  substitutions      = {}
  tags               = []
  depends_on         = [google_project_service.ci]
  approval_config {
    approval_required = false
  }
  github {
    enterprise_config_resource_name = null
    name                            = var.github_repo
    owner                           = var.github_owner
    push {
      branch       = null
      invert_regex = false
      tag          = "^v.*"
    }
  }
}
# Run the tests on pull requests to main.
#
# Until this existed the only trigger was publish-on-tag, so tests ran at
# release time only -- able to stop a bad publish, never a bad merge.
resource "google_cloudbuild_trigger" "test_on_pr" {
  description = "Run lint, unit tests and tier 1 on PRs to main"
  disabled    = false
  filename    = "cloudbuild.test.yaml"
  location    = var.region
  name        = "test-on-pr"
  project     = var.project_id

  # NOT the ci-runner: see the comment on google_service_account.ci_test.
  # A PR build runs code from the contributor's branch, and ci-runner can mint
  # a PyPI publishing token.
  service_account = "projects/${var.project_id}/serviceAccounts/${google_service_account.ci_test.email}"

  depends_on = [
    google_project_service.ci,
    google_project_iam_member.test_log_writer,
  ]

  approval_config {
    approval_required = false
  }

  github {
    name  = var.github_repo
    owner = var.github_owner
    pull_request {
      branch = var.trigger_branch_regex

      # Your own PRs build immediately; a PR from a fork waits for an owner to
      # comment /gcbrun. Without this, a fork PR runs its own build config on
      # your project's billing account the moment it is opened.
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
      invert_regex    = false
    }
  }
}

# The full e2e suite, on demand only.
#
# No push or pull_request block: this is a manual trigger, run from the console
# or `gcloud builds triggers run e2e-manual`. That is deliberate. The suite
# drives five real coding agents making real model calls, so every run costs
# money and minutes -- fine when you are confirming a regression against a
# pinned harness version, wrong as a gate on every commit.
#
# Harness versions are build args in the Dockerfile, so to test a suspect
# release:
#   gcloud builds triggers run e2e-manual --substitutions=_GROK_VERSION=1.0.4
resource "google_cloudbuild_trigger" "e2e_manual" {
  description = "Run the full integration suite against real agents. Manual."
  disabled    = false
  location    = var.region
  name        = "e2e-manual"
  project     = var.project_id

  # Reads the API keys; cannot publish. See google_service_account.ci_e2e.
  service_account = "projects/${var.project_id}/serviceAccounts/${google_service_account.ci_e2e.email}"

  depends_on = [
    google_project_service.ci,
    google_project_iam_member.e2e_log_writer,
    google_secret_manager_secret_iam_member.e2e_accessor,
  ]

  source_to_build {
    uri       = local.repo_uri
    ref       = "refs/heads/main"
    repo_type = "GITHUB"
  }

  # Pinned to main, and not to whatever branch the run was launched from.
  #
  # A manual trigger has to name a revision for its build config, and this one
  # runs as ci_e2e -- the only identity here that can read the provider API
  # keys. A config that followed a branch would let anyone who can land a
  # branch rewrite cloudbuild.e2e.yaml and have it executed by an identity
  # holding three vendors' keys. That is the exposure ci_test exists to close
  # for pull requests, applied to the one trigger that is not powerless.
  #
  # The cost is a slower edit loop: `--branch` moves source_to_build but not
  # this, so a change to cloudbuild.e2e.yaml is silently ignored until it
  # reaches main. That is the right way round -- a build config a contributor
  # can supply is worth more to an attacker than an afternoon is to us.
  git_file_source {
    path      = "cloudbuild.e2e.yaml"
    uri       = local.repo_uri
    revision  = "refs/heads/main"
    repo_type = "GITHUB"
  }
}

# Build both images, but only when a pull request touches something that
# changes them.
#
# Both were steps in cloudbuild.test.yaml, running on every PR: 92s for the
# agents target and 24s for the ci one, on changes to markdown or terraform
# that could not affect either. Splitting them out leaves the expensive checks
# on the PRs that can actually break them.
#
# Shares ci-test: this builds an image and pushes nothing, so it needs no more
# privilege than running the tests does.
resource "google_cloudbuild_trigger" "build_images_on_pr" {
  description     = "Build the ci and agents images when the files that shape them change"
  disabled        = false
  filename        = "cloudbuild.image.yaml"
  location        = var.region
  name            = "build-images-on-pr"
  project         = var.project_id
  service_account = "projects/${var.project_id}/serviceAccounts/${google_service_account.ci_test.email}"

  # The whole point. Only these paths change what the images contain: the
  # recipe, the runtime credential wiring, what enters the build context, this
  # trigger's own build config, and the dependency set.
  #
  # pyproject.toml and uv.lock are here because the ci target's whole assertion
  # is that `uv sync --group dev` still resolves and the package still imports.
  # A dependency change is the one thing that can break that.
  #
  # Deliberately NOT src/** or tests/**: nothing about a python change can break
  # an installer, and the test trigger already runs the suite on every PR.
  included_files = [
    "Dockerfile",
    "docker-entrypoint.sh",
    ".dockerignore",
    "cloudbuild.image.yaml",
    "pyproject.toml",
    "uv.lock",
  ]

  depends_on = [
    google_project_service.ci,
    google_project_iam_member.test_log_writer,
  ]

  approval_config {
    approval_required = false
  }

  github {
    name  = var.github_repo
    owner = var.github_owner
    pull_request {
      branch          = var.trigger_branch_regex
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
      invert_regex    = false
    }
  }
}


# Deploy the cloud server to STAGING on a `cloud-v*` tag.
#
# Its own tag namespace, not `v*`. The package and the server have no reason to
# ship together: coupling them would mean a docs-only release redeploying an
# internet-facing OAuth server, and a server fix waiting on a package it did
# not touch.
#
# Staging only. Production's image lives in infra/cloud/terraform.tfvars and is
# applied by hand -- CI could not apply it anyway, because the state is local
# to a laptop and gitignored.
resource "google_cloudbuild_trigger" "deploy_cloud_on_tag" {
  deletion_policy = "PREVENT"
  description     = "Build the cloud server and deploy it to staging, on cloud-v*"
  disabled        = false
  filename        = "cloudbuild.deploy.yaml"
  location        = var.region
  name            = "deploy-cloud-on-tag"
  project         = var.project_id

  # ci-runner, not ci-test: this one pushes an image and updates a service, so
  # it needs an identity that can. It is tag-triggered, so it never runs a
  # contributor's branch -- which is the reason ci-test exists and why this may
  # hold real permissions.
  service_account = "projects/${var.project_id}/serviceAccounts/${var.project_id}-ci-runner@${var.project_id}.iam.gserviceaccount.com"

  depends_on = [google_project_service.ci]

  approval_config {
    approval_required = false
  }

  github {
    name  = var.github_repo
    owner = var.github_owner
    push {
      tag = "^cloud-v.*"
    }
  }
}

# Cross-project, and the only such reach in either stack. The build runs in
# agent-bus-build; the registry and the service live in agent-bus-cloud.
#
# infra/cloud deliberately has no cross-project IAM -- images are built in the
# project that runs them -- and this is the exception that buys tag-triggered
# deploys.
#
# Both grants are on the single resource CI touches, not on the project. They
# were project-level until #122: `roles/run.developer` on agent-bus-cloud let
# the tag runner update PRODUCTION, and the only thing that kept it in staging
# was the `_SERVICE` substitution in cloudbuild.deploy.yaml -- a default a
# manual trigger run can override. A convention doing a control's job, which is
# what the four service accounts in this stack exist to avoid.

# What `gcloud run services update` needs, on the one service it may update.
#
# Two read-only probes say this is enough; neither is the deploy, so see the
# note below.
#
# `gcloud iam list-testable-permissions` on this service resource returns 15 of
# run.developer's 89 permissions, and `run.services.get` and
# `run.services.update` -- the two the update needs -- are among them. What is
# NOT service-grantable is `run.operations.*`, `run.locations.list` and
# `resourcemanager.projects.get`, so the question was whether gcloud calls any
# of those on the way.
#
# It does not. `--log-http` on a describe shows exactly one endpoint:
#
#   https://us-central1-run.googleapis.com/apis/serving.knative.dev/v1/
#     namespaces/agent-bus-cloud/services/agent-bus-staging
#
# The v1 Knative API, which is synchronous -- no long-running operation to
# poll, so no `run.operations.get`. And because cloudbuild.deploy.yaml passes
# both `--region` and `--project` explicitly, there is no location or project
# lookup either.
#
# **Unverified: the update path beyond the read.** The PUT goes to that same
# service endpoint and the readiness poll re-reads that same resource, so the
# risk is small -- but a describe is not an update, and only a deploy settles
# it. If a `cloud-v*` build fails with 403 on the deploy-staging step, this is
# why: restore the project-level grant and say so here rather than leaving a
# claim that is not true. That is what the comment this replaces got wrong.
resource "google_cloud_run_v2_service_iam_member" "ci_runner_updates_staging" {
  project  = var.cloud_project_id
  location = var.cloud_region
  name     = "agent-bus-staging"
  role     = "roles/run.developer"
  member   = local.ci_runner
}

# What `docker push` needs, on the one repository it may push to.
#
# The independent half, and the cheap one: repository-level artifactregistry
# IAM is ordinary and well-trodden, so this half would have been worth taking
# even if the run half had to stay project-wide.
resource "google_artifact_registry_repository_iam_member" "ci_runner_pushes_images" {
  project    = var.cloud_project_id
  location   = var.cloud_region
  repository = "cloud"
  role       = "roles/artifactregistry.writer"
  member     = local.ci_runner
}

# Cloud Run deploys as the service's own runtime identity, so whoever updates
# the service must be allowed to act as it.
resource "google_service_account_iam_member" "ci_runner_acts_as_staging_runtime" {
  service_account_id = "projects/${var.cloud_project_id}/serviceAccounts/agent-bus-staging-run@${var.cloud_project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = local.ci_runner
}
