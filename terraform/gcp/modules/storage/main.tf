resource "google_storage_bucket" "registry" {
  name          = var.bucket_name
  location      = var.region
  storage_class = "STANDARD"
  project       = var.project_id

  uniform_bucket_level_access = true

  versioning {
    enabled = var.enable_versioning
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.lifecycle_age_days
      matches_prefix = ["runs/", "traces/"]
    }
  }

  force_destroy = false
}

resource "google_storage_bucket_iam_member" "registry_admin" {
  bucket = google_storage_bucket.registry.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.service_account_email}"
}
