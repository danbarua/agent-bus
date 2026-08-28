resource "google_cloud_run_v2_service" "staging" {
  project  = var.project_id
  name     = "agent-bus-staging"
  location = var.region

  # Public, for the same reason production is: an MCP connector pings
  # `initialize` and `tools/list` before it ever attaches a bearer, so gating
  # at the network means no tool is visible at all. Authorization is the
  # server's job. See cloud/README.md.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      # The `run.app` URL is the issuer. Staging has no domain mapping, so
      # there is no stable hostname to name here -- and that is fine, because
      # nothing caches this issuer across a redeploy the way a real connector
      # would. Set explicitly after the first apply if a connector is ever
      # pointed at staging.
      env {
        name  = "AGENT_BUS_CLOUD_ISSUER"
        value = "https://agent-bus-staging-placeholder.invalid"
      }

      # THE line that makes this staging rather than a second front door onto
      # production. Unset would mean `(default)`.
      env {
        name  = "AGENT_BUS_CLOUD_DATABASE"
        value = google_firestore_database.staging.name
      }

      env {
        name  = "AGENT_BUS_CLOUD_ALLOWLIST"
        value = jsonencode(var.allowlist)
      }

      # For the log trace field, not for Firestore -- the client resolves the
      # project from the metadata server.
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name = "AGENT_BUS_CLOUD_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging["staging-signing-key"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "AGENT_BUS_CLOUD_PASSPHRASE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging["staging-consent-passphrase"].secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      # The server refuses to start without a signing key, so a misconfigured
      # revision fails the probe and never takes traffic.
      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 2
        period_seconds        = 3
        failure_threshold     = 5
      }
    }
  }

  # CI owns the image here; terraform owns everything else.
  #
  # `deploy-cloud-on-tag` updates this service on a `cloud-v*` tag, because CI
  # cannot run terraform at all -- the state is local to a laptop and
  # gitignored, deliberately. Without this, the next `terraform apply` would
  # silently revert staging to whatever `var.image` happened to say, undoing a
  # deploy nobody remembered was out of band.
  #
  # Production does NOT do this: its image stays in terraform.tfvars and is
  # applied by hand, because a promotion should be a decision and terraform
  # should be able to tell you what is running.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_secret_manager_secret_iam_member.runtime_accessor]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.staging.location
  name     = google_cloud_run_v2_service.staging.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
