output "ci_runner_email" {
  description = "The identity every trigger runs as. Holds roles/logging.logWriter and nothing else."
  value       = google_service_account.ci_runner.email
}

