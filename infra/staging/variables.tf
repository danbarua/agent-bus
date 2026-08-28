variable "project_id" {
  # A tenant of the project infra/cloud creates, not a project of its own.
  # No default that could point somewhere unintended.
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "database" {
  # The Firestore database this service reads and writes. NOT `(default)` --
  # that is production's, and a staging service sharing it would be a second
  # front end onto production's records rather than an environment.
  type    = string
  default = "staging"
}

variable "image" {
  # From the SAME Artifact Registry repository production uses, because staging
  # exists to run the artefact that is about to be promoted. The default is
  # Google's hello container so a first apply completes before anything has
  # been built.
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "max_instances" {
  # Lower than production's. Nothing here serves anyone, and an unbounded
  # staging service is an unbounded bill for something nobody is watching.
  type    = number
  default = 2
}

variable "allowlist" {
  # Redirect URI -> peer address, as production. Empty by default: staging has
  # no connectors, and adding one here would make AGENT_BUS_CLOUD_PASSPHRASE
  # required -- see cloud/README.md.
  type    = map(string)
  default = {}
}
