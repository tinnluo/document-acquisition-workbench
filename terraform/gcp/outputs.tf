output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

output "region" {
  description = "GCP region"
  value       = var.region
}

output "environment" {
  description = "Environment name"
  value       = var.environment
}

output "gcs_registry_bucket" {
  description = "GCS bucket name for registry storage"
  value       = module.storage.bucket_name
}

output "cloud_run_job_name" {
  description = "Cloud Run Job name"
  value       = module.cloud_run_job.job_name
}

output "service_account_email" {
  description = "Service account email for the Cloud Run Job"
  value       = google_service_account.job_sa.email
}

output "secret_id" {
  description = "Secret Manager secret ID"
  value       = module.secrets.secret_id
}

output "scheduler_name" {
  description = "Cloud Scheduler job name (if enabled)"
  value       = var.enable_scheduler ? module.scheduler[0].scheduler_name : null
}

output "artifact_registry_url" {
  description = "Artifact Registry repository URL"
  value       = module.artifact_registry.repository_url
}

output "manual_execution_command" {
  description = "Command to manually execute the job"
  value       = "gcloud run jobs execute ${module.cloud_run_job.job_name} --region=${var.region}"
}
