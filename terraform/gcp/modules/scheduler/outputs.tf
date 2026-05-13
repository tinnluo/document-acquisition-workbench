output "scheduler_name" {
  description = "The name of the Cloud Scheduler job"
  value       = var.enabled ? google_cloud_scheduler_job.periodic[0].name : null
}

output "scheduler_id" {
  description = "The ID of the Cloud Scheduler job"
  value       = var.enabled ? google_cloud_scheduler_job.periodic[0].id : null
}
