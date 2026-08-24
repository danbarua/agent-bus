# Credentials for the manual e2e trigger.
#
# Only the secret CONTAINERS are declared here. Versions -- the actual keys --
# are added by hand:
#
#   printf %s "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-
#   printf %s "$OPENAI_API_KEY"    | gcloud secrets versions add openai-api-key    --data-file=-
#   printf %s "$XAI_API_KEY"       | gcloud secrets versions add xai-api-key       --data-file=-
#
# Deliberately not google_secret_manager_secret_version resources: those take
# the value as an argument, which writes it to terraform state in plaintext.
# State is gitignored here, but "the secret is safe because a .gitignore entry
# is correct" is not a property worth depending on.
locals {
  e2e_secret_ids = toset([
    "anthropic-api-key", # claude peer (tiers 3-4) and pi
    "openai-api-key",    # codex
    "xai-api-key",       # grok and omp
  ])
}

resource "google_secret_manager_secret" "e2e" {
  for_each  = local.e2e_secret_ids
  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.ci]
}

# Read access for the e2e runner only. Neither the PR runner nor the publisher
# can read these.
resource "google_secret_manager_secret_iam_member" "e2e_accessor" {
  for_each  = google_secret_manager_secret.e2e
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ci_e2e.email}"
}
