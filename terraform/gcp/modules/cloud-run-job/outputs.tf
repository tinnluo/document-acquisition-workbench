output "job_name" {
  description = "The name of the Cloud Run Job"
  value       = google_cloud_run_v2_job.main.name
}

output "job_id" {
  description = "The ID of the Cloud Run Job"
  value       = google_cloud_run_v2_job.main.id
}

output "job_location" {
  description = "The location of the Cloud Run Job"
  value       = google_cloud_run_v2_job.main.location
}
