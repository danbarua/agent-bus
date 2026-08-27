variable "billing_account_id" {
  type = string
}

variable "project_id" {
  type    = string
  default = "agent-bus-cloud"
}

variable "region" {
  # us-central1 is one of the ten regions that support Cloud Run domain
  # mappings. Most do not, and the mapping resource fails at apply rather than
  # at plan, so this is not a free choice:
  #   asia-east1, asia-northeast1, asia-southeast1, europe-north1,
  #   europe-west1, europe-west4, us-central1, us-east1, us-east4, us-west1
  type    = string
  default = "us-central1"
}

variable "hostname" {
  # The OAuth `issuer` and the base of every URL a connector caches. Moving it
  # after a connector registers invalidates all of them, so treat it as
  # permanent rather than as configuration.
  type    = string
  default = "agent-bus.framesift.ai"
}

variable "image" {
  # Chicken and egg: Cloud Run cannot deploy an image that does not exist yet,
  # and Artifact Registry does not exist until this stack is applied. The
  # default is Google's own hello container so the first apply completes --
  # which is what gets the domain mapping created and its TLS certificate
  # provisioning, the slowest part of the whole exercise. Push the real image
  # and set this afterwards. See README.
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "max_instances" {
  # The spend cap, and the only one. Cloud Run bills per request-second; an
  # unbounded service on a public hostname is an unbounded bill. Four is far
  # more than one person's two connectors and a bridge will ever need.
  type    = number
  default = 4
}

variable "allowlist" {
  # Redirect URI -> peer address. The redirect URI is the only thing in the
  # OAuth flow that names the vendor, and it is one we control rather than one
  # the client asserts.
  #
  # Empty is a valid deployment: a bridge token is minted out of band, so the
  # bus works with no connector attached. Adding an entry here is what makes
  # AGENT_BUS_CLOUD_PASSPHRASE required -- see cloud/README.md.
  type    = map(string)
  default = {}
}
