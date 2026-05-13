variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud Run Job"
  type        = string
}

variable "job_name" {
  description = "Name of the Cloud Run Job"
  type        = string
}

variable "image_uri" {
  description = "Full URI of the container image"
  type        = string
}

variable "service_account_email" {
  description = "Service account email for the job"
  type        = string
}

variable "gcs_registry_bucket" {
  description = "Name of the GCS bucket to mount"
  type        = string
}

variable "search_api_key_secret" {
  description = "Secret Manager secret ID for search API key"
  type        = string
}

variable "search_api_key_env_var" {
  description = "Environment variable name for search API key (SERPER_API_KEY or BRAVE_API_KEY)"
  type        = string
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

variable "cpu" {
  description = "CPU allocation for the job"
  type        = string
  default     = "2"
}

variable "memory" {
  description = "Memory allocation for the job"
  type        = string
  default     = "2Gi"
}

variable "job_args" {
  description = "Arguments to pass to the container"
  type        = list(string)
  default     = ["discover", "--entities", "/mnt/gcs/entities.csv", "--workspace-root", "/mnt/gcs"]
}
