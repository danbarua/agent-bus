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
  description = "Run tests and build both images on PRs to main"
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
