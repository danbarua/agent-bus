locals {
  repo_uri       = "https://github.com/${var.github_owner}/${var.github_repo}"
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