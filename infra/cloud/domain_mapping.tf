# bus.example.com already CNAMEs to ghs.googlehosted.com -- which is
# exactly the record a Cloud Run domain mapping needs for a subdomain. Nothing
# to repoint. HTTPS fails today only because this resource does not exist.
#
# **example.com must be verified in Search Console under the account running
# this apply, or creation fails.** Verifying a subdomain is not enough; it is
# the base domain that must be verified. See README -- it is a pre-apply step.
#
# Domain mappings are a Preview feature and Google's own documentation
# recommends a global external Application Load Balancer instead, citing
# latency. The ALB is roughly $20/month before a single request, needs the DNS
# repointed to an A record, and replaces one resource with six. For one person's
# message bus the mapping is the right trade; this comment is the escape hatch
# if it ever bites.
resource "google_cloud_run_domain_mapping" "bus" {
  project  = var.project_id
  location = google_cloud_run_v2_service.bus.location
  name     = var.hostname

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.bus.name
  }
}
