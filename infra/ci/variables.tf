variable "billing_account_id" {
  type = string
}

variable "project_id" {
  type    = string
  default = "agent-bus-build"
}

variable "project_name" {
  type    = string
  default = "agent-bus-build"
}

variable "project_number" {
  description = "Numeric project id. Only used to name Google-managed service agents."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "github_owner" {
  type    = string
  default = "danbarua"
}

variable "github_repo" {
  type    = string
  default = "agent-bus"
}

variable "trigger_branch_regex" {
  type    = string
  default = "^(main)$"
}

variable "cloud_project_id" {
  # The project the server runs in. This stack does not create or manage it --
  # infra/cloud does -- it only grants the tag runner enough to push an image
  # and update the staging service there.
  type    = string
  default = "agent-bus-cloud"
}
