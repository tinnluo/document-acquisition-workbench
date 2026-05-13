resource "google_cloud_run_v2_job" "main" {
  name     = var.job_name
  location = var.region
  project  = var.project_id

  template {
    template {
      max_retries = var.job_max_retries
      timeout     = "${var.job_task_timeout}s"

      service_account = var.service_account_email

      # Cloud Storage volume mount
      volumes {
        name = "gcs-registry"
        gcs {
          bucket    = var.gcs_registry_bucket
          read_only = false
        }
      }

      containers {
        image = var.image_uri
        args  = var.job_args

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        # Mount GCS bucket
        volume_mounts {
          name       = "gcs-registry"
          mount_path = "/mnt/gcs"
        }

        # Environment variables aligned with actual code
        env {
          name  = "DOC_WORKBENCH_HOME"
          value = "/mnt/gcs"
        }

        env {
          name  = "DOC_WORKBENCH_ENGINE"
          value = "legacy"
        }

        # Search API key from Secret Manager
        env {
          name = var.search_api_key_env_var
          value_source {
            secret_key_ref {
              secret  = var.search_api_key_secret
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      launch_stage,
    ]
  }
}

# Grant service account permission to invoke the job
resource "google_cloud_run_v2_job_iam_member" "invoker" {
  name     = google_cloud_run_v2_job.main.name
  location = google_cloud_run_v2_job.main.location
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.service_account_email}"
}
