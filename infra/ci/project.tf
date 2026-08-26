terraform {
  required_version = ">= 1.15.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

# Create a project with billing linked from the start
resource "google_project" "build_project" {
  deletion_policy = "PREVENT"
  # Matches the live project's display name. It was "agent-bus cloud-build
  # project" here, which made adoption want to RENAME the real project -- a
  # cosmetic change, but not one an import should make on your behalf. Change
  # it deliberately if you want the longer name.
  name            = "agent-bus-build"
  project_id      = "agent-bus-build"
  billing_account = var.billing_account_id

  # Auto-create default service account
  auto_create_network = true
}