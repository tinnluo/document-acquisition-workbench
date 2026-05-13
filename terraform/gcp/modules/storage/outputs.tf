output "bucket_name" {
  description = "The name of the GCS bucket"
  value       = google_storage_bucket.registry.name
}

output "bucket_url" {
  description = "The URL of the GCS bucket"
  value       = google_storage_bucket.registry.url
}

output "bucket_self_link" {
  description = "The self link of the GCS bucket"
  value       = google_storage_bucket.registry.self_link
}
