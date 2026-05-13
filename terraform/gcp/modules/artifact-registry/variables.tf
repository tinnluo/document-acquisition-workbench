variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the Artifact Registry repository"
  type        = string
}

variable "repository_id" {
  description = "The ID of the Artifact Registry repository"
  type        = string
}

variable "description" {
  description = "Description of the Artifact Registry repository"
  type        = string
  default     = "Document acquisition workbench container images"
}
