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
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:${google_service_account.ci_runner.email}"
  depends_on = [google_service_account.ci_runner]
}