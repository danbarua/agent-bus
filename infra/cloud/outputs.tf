output "service_url" {
  description = "The run.app URL. Works immediately; the custom hostname takes longer."
  value       = google_cloud_run_v2_service.bus.uri
}

output "issuer" {
  description = "The OAuth issuer. Every connector caches URLs derived from it, so it must not move."
  value       = "https://${var.hostname}"
}

output "runtime_service_account" {
  description = "Holds roles/datastore.user and read on its own two secrets. Nothing else."
  value       = google_service_account.runtime.email
}

output "image_repository" {
  description = "Push the server image here, then set the `image` variable to a tag in it."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# Read from the resource, never hardcoded. The mapping reports the records it
# actually wants, and for a subdomain that is normally the CNAME already in
# place -- but a hardcoded list would be a second copy to drift.
output "dns_records" {
  description = "What the mapping wants in DNS. Compare with what example.com already serves."
  value = [
    for r in google_cloud_run_domain_mapping.bus.status[0].resource_records :
    "${r.type} ${r.name} ${r.rrdata}"
  ]
}
