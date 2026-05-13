locals {
  secret_id    = var.search_provider == "serper" ? "serper-api-key" : "brave-api-key"
  env_var_name = var.search_provider == "serper" ? "SERPER_API_KEY" : "BRAVE_API_KEY"
}

resource "google_secret_manager_secret" "search_api_key" {
  secret_id = local.secret_id
  project   = var.project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "search_api_key_accessor" {
  secret_id = google_secret_manager_secret.search_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_account_email}"
}
