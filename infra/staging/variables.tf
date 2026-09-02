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
  #
  # **Production removed this default and staging keeps it, on purpose.** Not
  # an inconsistency to tidy up. Production is stood up once and never again,
  # so its default outlived its only use and became a way to replace a live
  # service with a demo page by forgetting a line in a tfvars. Staging is
  # disposable -- torn down and recreated whenever an isolated environment is
  # wanted -- so here the bootstrap case is the recurring one.
  #
  # Inert after that first apply either way: `run.tf` has `ignore_changes` on
  # the image because CI owns it, so terraform never writes this value again.
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
