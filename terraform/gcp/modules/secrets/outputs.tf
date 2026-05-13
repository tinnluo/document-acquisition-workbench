output "secret_id" {
  description = "The ID of the secret"
  value       = google_secret_manager_secret.search_api_key.secret_id
}

output "secret_name" {
  description = "The full resource name of the secret"
  value       = google_secret_manager_secret.search_api_key.name
}

output "env_var_name" {
  description = "The environment variable name for this secret (SERPER_API_KEY or BRAVE_API_KEY)"
  value       = local.env_var_name
}
