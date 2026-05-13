variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "image_uri" {
  description = "Full URI of the container image in Artifact Registry"
  type        = string
}

variable "search_provider" {
  description = "Search provider to use (serper or brave)"
  type        = string
  default     = "serper"
  validation {
    condition     = contains(["serper", "brave"], var.search_provider)
    error_message = "Search provider must be either 'serper' or 'brave'."
  }
}

variable "job_max_retries" {
  description = "Maximum number of retries for failed job executions"
  type        = number
  default     = 3
}

variable "job_task_timeout" {
  description = "Task timeout in seconds"
  type        = number
  default     = 1800
}

variable "gcs_lifecycle_age_days" {
  description = "Age in days after which runs/ and traces/ are deleted"
  type        = number
  default     = 30
}

variable "enable_scheduler" {
  description = "Whether to create Cloud Scheduler job for periodic triggers"
  type        = bool
  default     = false
}

variable "scheduler_cron" {
  description = "Cron schedule for periodic job execution"
  type        = string
  default     = "0 2 * * *"
}
