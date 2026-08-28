resource "google_cloud_run_v2_service" "bus" {
  project  = var.project_id
  name     = "agent-bus"
  location = var.region

  # Public. The MCP surface has to answer an anonymous `initialize` and
  # `tools/list` before a connector ever attaches a bearer -- gating at the
  # network would mean no tool is visible at all, whether or not auth works.
  # Authorization is the server's job and is tested as such; see cloud/README.md.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email

    scaling {
      # Scale to nothing between messages. A bridge polls; a connector is idle
      # most of the day. Cold starts cost a second and save the standing charge.
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      env {
        name  = "AGENT_BUS_CLOUD_ISSUER"
        value = "https://${var.hostname}"
      }

      # Not for Firestore -- the client resolves that from the metadata server.
      # This is for the log trace field: Cloud Logging groups on
      # `projects/<id>/traces/<id>`, so the server needs the project id to
      # build one from the X-Cloud-Trace-Context header Cloud Run sends. Absent
      # it the field is omitted and app logs stop nesting under the request
      # they belong to, which is the whole reason the header is read.
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      # Config, not a secret: it names public callback URLs. Keeping it out of
      # Secret Manager means changing which connectors may attach is a plan you
      # can read in a diff.
      env {
        name  = "AGENT_BUS_CLOUD_ALLOWLIST"
        value = jsonencode(var.allowlist)
      }

      env {
        name = "AGENT_BUS_CLOUD_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cloud["cloud-signing-key"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "AGENT_BUS_CLOUD_PASSPHRASE"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cloud["cloud-consent-passphrase"].secret_id
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
      # revision fails its startup probe and never takes traffic. The previous
      # revision keeps serving. That is the #78 fix earning its keep at deploy
      # time: the failure it prevents is a container that answers /health
      # perfectly while authenticating nobody.
      #
      # Both secrets must already hold a version before this can succeed --
      # a container with none has no `latest` to mount. See README: that is
      # what the targeted first apply is for.
      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 2
        period_seconds        = 3
        failure_threshold     = 5
      }
    }
  }

  depends_on = [
    google_project_service.cloud,
    google_secret_manager_secret_iam_member.runtime_accessor,
  ]
}

# Anonymous access at the network layer. See `ingress` above -- discovery must
# answer without a bearer or no connector can find a tool to call.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.bus.location
  name     = google_cloud_run_v2_service.bus.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
