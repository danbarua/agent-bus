output "service_url" {
  description = "Staging is reached here. No domain mapping, by design."
  value       = google_cloud_run_v2_service.staging.uri
}

output "database" {
  description = "The Firestore database this service reads and writes. Never `(default)`."
  value       = google_firestore_database.staging.name
}

output "runtime_service_account" {
  value       = google_service_account.runtime.email
  description = "Its own identity, with its own secrets."
}
