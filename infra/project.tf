terraform {
  required_version = ">= 1.15.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

# The project already exists -- it was created by hand, before this config, and
# builds run in it today. It was never in state, so a plan said "will be
# created" and an apply would have tried to create a project whose id is taken
# and failed on that resource.
#
# This is the declarative form of `terraform import`: on the next apply
# Terraform adopts the existing project instead of creating one. It is
# non-destructive -- nothing in the cloud changes, only state gains a record.
#
# Safe to delete this block once an apply has run; it is a one-time adoption,
# and leaving it costs nothing but says less each time it is read.
import {
  to = google_project.build_project
  id = "agent-bus-build"
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