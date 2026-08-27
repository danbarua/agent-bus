terraform {
  required_version = ">= 1.15.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

# Separate from agent-bus-build on purpose. That project runs CI: it holds the
# identity that can publish to PyPI and the provider API keys the e2e tier
# spends money with. This one is reachable from the public internet by anyone
# who reads a CT log, so nothing in it should be able to reach either of those.
resource "google_project" "cloud" {
  deletion_policy = "PREVENT"
  name            = var.project_id
  project_id      = var.project_id
  billing_account = var.billing_account_id

  auto_create_network = true
}
