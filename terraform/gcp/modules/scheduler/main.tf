resource "google_cloud_scheduler_job" "periodic" {
  count = var.enabled ? 1 : 0

  name             = var.scheduler_name
  description      = "Periodic trigger for ${var.cloud_run_job_name}"
  schedule         = var.schedule_cron
  time_zone        = var.time_zone
  attempt_deadline = "320s"
  region           = var.region
  project          = var.project_id

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.cloud_run_job_location}/jobs/${var.cloud_run_job_name}:run"

    oauth_token {
      service_account_email = var.service_account_email
    }
  }
}
