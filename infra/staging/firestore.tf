# Its own database, and this is the isolation that matters most day to day.
#
# `cloud/store.py` takes a `database` argument and `AGENT_BUS_CLOUD_DATABASE`
# drives it; unset means `(default)`, which is what production runs. Without
# that, a second Cloud Run service in this project would write straight into
# production's records -- which is not a staging environment, it is a second
# front door.
resource "google_firestore_database" "staging" {
  project     = var.project_id
  name        = var.database
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Not PREVENT: this one is meant to be disposable. That is the whole point of
  # it, and production's `(default)` is protected separately in infra/cloud.
  deletion_policy = "DELETE"
}

# The same three collection groups production expires, for the same reasons --
# the list lives in the `Firestore` class docstring in cloud/store.py, which is
# where both stacks have to agree with it.
#
# Not oauth_clients: ChatGPT caches its client_id indefinitely, so expiring a
# registration orphans a live connector.
locals {
  ttl_collections = toset(["items", "roster", "oauth_codes"])
}

resource "google_firestore_field" "expire_at" {
  for_each = local.ttl_collections

  project    = var.project_id
  database   = google_firestore_database.staging.name
  collection = each.value
  field      = "expireAt"

  ttl_config {}

  # No index on a monotonically increasing timestamp -- the textbook Firestore
  # write hotspot, and nothing queries by it.
  index_config {}
}
