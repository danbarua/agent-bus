# One identity, one job. The service reads and writes messages and nothing
# else: it does not deploy, does not build, cannot read the CI project's
# provider API keys, and cannot reach anything outside this project.
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.project_id}-run"
  display_name = "agent-bus cloud server"
  description  = "Runs the Cloud Run service. Firestore and its own secrets, nothing more."

  depends_on = [google_project_service.cloud]
}

# roles/datastore.user, not editor or owner. It covers document read/write and
# stops short of managing indexes, TTL policies or the database itself -- all
# of which are this stack's job, not the running server's.
resource "google_project_iam_member" "runtime_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}
