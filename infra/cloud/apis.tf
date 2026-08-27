resource "google_project_service" "cloud" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
  ])

  project = google_project.cloud.project_id
  service = each.value

  timeouts {
    create = "30m"
    update = "40m"
  }

  disable_on_destroy         = false
  disable_dependent_services = false
}
