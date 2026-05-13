variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the storage bucket"
  type        = string
}

variable "bucket_name" {
  description = "Name of the GCS bucket for registry storage"
  type        = string
}

variable "service_account_email" {
  description = "Service account email to grant storage access"
  type        = string
}

variable "lifecycle_age_days" {
  description = "Age in days after which runs/ and traces/ are deleted"
  type        = number
  default     = 30
}

variable "enable_versioning" {
  description = "Enable object versioning on the bucket"
  type        = bool
  default     = true
}
