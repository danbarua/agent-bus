# Its own identity, so a staging compromise cannot read production's records.
#
# Sharing production's runtime service account would have made every other
# boundary here cosmetic: the database name is chosen by configuration, and an
# identity that can read both databases is one environment variable away from
# reading the wrong one.
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "agent-bus-staging-run"
  display_name = "agent-bus staging server"
  description  = "Runs the staging Cloud Run service. Its own key, its own database."
}

# roles/datastore.user is project-wide -- Firestore IAM does not scope to a
# single database at this level. So this identity CAN reach production's
# `(default)`, and the thing that stops it is that the service is never told
# to: AGENT_BUS_CLOUD_DATABASE names `staging` and the code has no fallback
# that would reach the other one.
#
# Worth stating plainly rather than implying an isolation that is not there.
# The isolation that IS real is the signing key: a staging token does not
# verify against production, whatever database anything points at.
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
