resource "google_project_service" "ci" {
  for_each = toset([
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
    # For the manual e2e trigger: the integration tiers drive real coding
    # agents, so the build needs three provider API keys at run time.
    "secretmanager.googleapis.com",
  ])

  project = var.project_id
  service = each.value


  timeouts {
    create = "30m"
    update = "40m"
  }

  disable_on_destroy         = false
  disable_dependent_services = false
}