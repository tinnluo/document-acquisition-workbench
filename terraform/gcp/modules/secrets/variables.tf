variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "service_account_email" {
  description = "Service account email to grant secret access"
  type        = string
}

variable "search_provider" {
  description = "Search provider to use (serper or brave)"
  type        = string
  validation {
    condition     = contains(["serper", "brave"], var.search_provider)
    error_message = "Search provider must be either 'serper' or 'brave'."
  }
}
