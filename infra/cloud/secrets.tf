# Containers only. Versions -- the actual values -- are added by hand:
#
#   printf %s "$(openssl rand -hex 32)" | gcloud secrets versions add cloud-signing-key    --data-file=- --project agent-bus-cloud
#   printf %s "a passphrase you can say out loud" | gcloud secrets versions add cloud-consent-passphrase --data-file=- --project agent-bus-cloud
#   printf %s "$(openssl rand -hex 32)" | gcloud secrets versions add cloud-webhook-github-secret --data-file=- --project agent-bus-cloud
#
# Deliberately not google_secret_manager_secret_version resources: those take
# the value as an argument, which writes it to terraform state in plaintext.
# Same rule as infra/ci, same reason.
#
# `printf %s`, never `echo`: a trailing newline becomes part of the secret. For
# the signing key that means every token verifies against a different key than
# the one that signed it, which looks exactly like a client bug.
locals {
  secret_ids = toset([
    "cloud-signing-key",        # HMAC key for every token this server mints
    "cloud-consent-passphrase", # the human half of the consent gate
    # One peer, one secret, holding the string GitHub signs with. A second
    # source is another secret and another mount -- both terraform, no code.
    "cloud-webhook-github-secret",
  ])
}

resource "google_secret_manager_secret" "cloud" {
  for_each  = local.secret_ids
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.cloud]
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  for_each  = google_secret_manager_secret.cloud
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
