locals {
  repo_uri = "https://github.com/${var.github_owner}/${var.github_repo}"
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

# The e2e tiers, on demand only.
#
# No push or pull_request block: this is a manual trigger, run from the console
# or `gcloud builds triggers run e2e-manual`. That is deliberate. The tiers
# drive five real coding agents making real model calls, so every run costs
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
