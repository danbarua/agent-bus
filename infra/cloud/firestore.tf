resource "google_firestore_database" "bus" {
  project = var.project_id
  name    = "(default)"

  # Effectively permanent. A database's location cannot be changed, and there
  # is one (default) database per project -- moving it means a new project.
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.cloud]
}

# The TTL policies.
#
# Three collection groups, and exactly three -- the list is in the `Firestore`
# class docstring in cloud/store.py, which is where it has to agree. Notably
# NOT oauth_clients: ChatGPT caches its client_id and reuses it indefinitely,
# so expiring a registration orphans a live connector.
#
# `expireAt` is a Timestamp because Firestore's TTL matches a `Date and time`
# field and nothing else. It was a float until #77, which would have made every
# one of these policies collect precisely nothing -- invisibly, since the server
# filters expired documents out of every read regardless.
locals {
  ttl_collections = toset(["items", "roster", "oauth_codes"])
}

resource "google_firestore_field" "expire_at" {
  for_each = local.ttl_collections

  project    = var.project_id
  database   = google_firestore_database.bus.name
  collection = each.value
  field      = "expireAt"

  ttl_config {}

  # No index on the TTL field. A monotonically increasing timestamp is the
  # textbook Firestore hotspot -- every write lands at the same end of the
  # index range -- and nothing queries by expireAt: the server filters in
  # memory, because TTL is a collector and not a filter.
  index_config {}
}
