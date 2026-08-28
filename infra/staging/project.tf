terraform {
  required_version = ">= 1.15.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

# No `google_project` resource, deliberately. This stack is a tenant of the
# project `infra/cloud` creates, and must never be able to alter or destroy it.
# The project id is an input; if it does not exist, this stack fails rather
# than creating a second one.
