# Containers only. Versions are added by hand, exactly as in infra/cloud:
#
#   printf %s "$(openssl rand -hex 32)" \
#     | gcloud secrets versions add staging-signing-key --data-file=- --project agent-bus-cloud
#
# Deliberately not google_secret_manager_secret_version resources -- those take
# the value as an argument and write it to terraform state in plaintext.
#
# **A DIFFERENT SIGNING KEY IS THE POINT OF THIS STACK.** Every other boundary
# here is tidiness. This one means a token minted for staging does not verify
# against production and a token minted for production does not verify here --
# so a mistake in one cannot reach a connector on the other, whatever else is
# misconfigured.
locals {
  secret_ids = toset([
    "staging-signing-key",
    "staging-consent-passphrase",
  ])
}

resource "google_secret_manager_secret" "staging" {
  for_each  = local.secret_ids
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  for_each  = google_secret_manager_secret.staging
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
