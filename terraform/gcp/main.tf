terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    # Backend configuration provided via -backend-config flag
    # See backend-{env}.hcl files
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required GCP APIs
resource "google_project_service" "required_apis" {
  for_each = toset([
    "run.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
  ])

  service            = each.key
  disable_on_destroy = false
}

# Service Account for Cloud Run Job
resource "google_service_account" "job_sa" {
  account_id   = "document-acquisition-sa"
  display_name = "Document Acquisition Workbench Service Account"
  description  = "Service account for Cloud Run Job execution"
  project      = var.project_id
}

# Grant necessary IAM roles to service account
resource "google_project_iam_member" "job_sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

# Artifact Registry Module
module "artifact_registry" {
  source = "./modules/artifact-registry"

  project_id    = var.project_id
  region        = var.region
  repository_id = "document-acquisition"

  depends_on = [google_project_service.required_apis]
}

# Storage Module
module "storage" {
  source = "./modules/storage"

  project_id            = var.project_id
  region                = var.region
  bucket_name           = "${var.project_id}-document-registry"
  service_account_email = google_service_account.job_sa.email
  lifecycle_age_days    = var.gcs_lifecycle_age_days

  depends_on = [google_project_service.required_apis]
}

# Secrets Module
module "secrets" {
  source = "./modules/secrets"

  project_id            = var.project_id
  service_account_email = google_service_account.job_sa.email
  search_provider       = var.search_provider

  depends_on = [google_project_service.required_apis]
}

# Cloud Run Job Module
module "cloud_run_job" {
  source = "./modules/cloud-run-job"

  project_id             = var.project_id
  region                 = var.region
  job_name               = "document-acquisition-job"
  image_uri              = var.image_uri
  service_account_email  = google_service_account.job_sa.email
  gcs_registry_bucket    = module.storage.bucket_name
  search_api_key_secret  = module.secrets.secret_id
  search_api_key_env_var = module.secrets.env_var_name
  job_max_retries        = var.job_max_retries
  job_task_timeout       = var.job_task_timeout

  depends_on = [
    google_project_service.required_apis,
    module.storage,
    module.secrets,
  ]
}

# Cloud Scheduler Module (conditional)
module "scheduler" {
  source = "./modules/scheduler"
  count  = var.enable_scheduler ? 1 : 0

  project_id             = var.project_id
  region                 = var.region
  scheduler_name         = "document-acquisition-daily"
  schedule_cron          = var.scheduler_cron
  cloud_run_job_name     = module.cloud_run_job.job_name
  cloud_run_job_location = module.cloud_run_job.job_location
  service_account_email  = google_service_account.job_sa.email
  enabled                = var.enable_scheduler

  depends_on = [
    google_project_service.required_apis,
    module.cloud_run_job,
  ]
}
