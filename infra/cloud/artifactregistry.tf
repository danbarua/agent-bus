# The image lives in the project that runs it. Same-project pulls need no
# cross-project IAM, and building here (`gcloud builds submit --project
# agent-bus-cloud`) means the CI project's identities never need write access
# to anything internet-facing.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "cloud"
  format        = "DOCKER"
  description   = "The agent-bus cloud server image. Built here, run here."

  # Keep the last few revisions and nothing else. Cloud Run rollback needs the
  # previous image to still exist; the twenty before it are storage cost.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  depends_on = [google_project_service.cloud]
}
