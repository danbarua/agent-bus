resource "google_project_service" "ci" {
  for_each = toset([
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
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