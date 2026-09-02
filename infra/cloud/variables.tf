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
  #
  # **No default, deliberately.** This is the address at which a person's
  # coding agents can be sent messages, and a default would put it in the
  # repository for anyone who reads it. It lives in terraform.tfvars, which is
  # gitignored, and nowhere else.
  type = string
}

variable "image" {
  # **No default, deliberately** -- the same reasoning as `hostname` above.
  #
  # It had one: Google's own hello container, so the very first apply could
  # complete before Artifact Registry existed and get the domain mapping's TLS
  # certificate provisioning started, which is the slowest part of standing
  # this up. That was true exactly once, and it has happened. What outlived it
  # was a config where forgetting one line replaces the production service with
  # Google's demo page -- silently, because a default is not a prompt.
  #
  # 2026-09-01: an apply without `-var-file` was one confirmation away from
  # emptying the OAuth redirect allowlist, for the same reason, on a different
  # variable. `image` is the same trap with a larger blast radius.
  #
  # If this stack is ever stood up again from nothing, pass the bootstrap
  # container explicitly:
  #
  #   terraform apply -var image=us-docker.pkg.dev/cloudrun/container/hello
  #
  # so that deploying a foreign container is a thing someone typed.
  type = string
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
