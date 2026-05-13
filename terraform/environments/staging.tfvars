project_id  = "document-acquisition-staging"
region      = "us-central1"
environment = "staging"

# Image URI (updated by CI/CD)
image_uri = "us-central1-docker.pkg.dev/document-acquisition-staging/document-acquisition/document-acquisition-workbench:latest"

# Search provider (serper or brave)
search_provider = "serper"

# Job configuration
job_max_retries  = 2
job_task_timeout = 2400 # 40 minutes

# Storage
gcs_lifecycle_age_days = 14

# Scheduler
enable_scheduler = false
