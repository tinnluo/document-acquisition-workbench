variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Scheduler job"
  type        = string
}

variable "scheduler_name" {
  description = "Name of the Cloud Scheduler job"
  type        = string
}

variable "schedule_cron" {
  description = "Cron schedule expression (e.g., '0 2 * * *')"
  type        = string
}

variable "time_zone" {
  description = "Time zone for the schedule"
  type        = string
  default     = "America/New_York"
}

variable "cloud_run_job_name" {
  description = "Name of the Cloud Run Job to trigger"
  type        = string
}

variable "cloud_run_job_location" {
  description = "Location of the Cloud Run Job"
  type        = string
}

variable "service_account_email" {
  description = "Service account email for authentication"
  type        = string
}

variable "enabled" {
  description = "Whether to create the scheduler job"
  type        = bool
  default     = false
}
