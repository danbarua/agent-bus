resource "google_service_account" "ci_runner" {
  account_id   = "${var.project_id}-ci-runner"
  display_name = "${var.project_name} CI runner"
  description  = "Runs Cloud Build and publishes package to PyPI."
}

resource "google_project_iam_member" "runner_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ci_runner.email}"
}

resource "google_project_iam_member" "act_as" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.ci_runner.email}"
}

resource "google_project_iam_member" "token_creator" {
  project    = var.project_id
  role       = "roles/iam.serviceAccountTokenCreator"
  member     = "serviceAccount:${google_service_account.ci_runner.email}"
  depends_on = [google_service_account.ci_runner]
}

# A second, deliberately powerless identity for pull-request builds.
#
# ci_runner above can mint an OIDC token for the PyPI trusted publisher
# (roles/iam.serviceAccountTokenCreator). A pull-request trigger executes the
# build config AND the Dockerfile from the contributor's branch, so running PR
# builds as ci_runner would let anyone who can open a pull request on a public
# repo execute arbitrary code as an identity that can publish to PyPI.
#
# This one can write logs and nothing else. If a PR build is compromised, the
# blast radius is a log line.
resource "google_service_account" "ci_test" {
  account_id   = "${var.project_id}-ci-test"
  display_name = "${var.project_name} PR test runner"
  description  = "Runs tests on pull requests. Deliberately cannot publish."
}

resource "google_project_iam_member" "test_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ci_test.email}"
}


# Identity for the manual e2e trigger.
#
# Separate from both others: it can read the provider API keys, which the PR
# runner must not, and it cannot mint a PyPI token, which the publisher can.
# Three triggers, three identities, no shared privilege.
resource "google_service_account" "ci_e2e" {
  account_id   = "${var.project_id}-ci-e2e"
  display_name = "${var.project_name} e2e runner"
  description  = "Runs the integration tiers against real coding agents. Manual trigger only."
}

resource "google_project_iam_member" "e2e_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.ci_e2e.email}"
}
