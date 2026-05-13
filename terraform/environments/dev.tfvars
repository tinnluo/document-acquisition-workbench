project_id  = "document-acquisition-dev"
region      = "us-central1"
environment = "dev"

# Image URI (updated by CI/CD)
image_uri = "us-central1-docker.pkg.dev/document-acquisition-dev/document-acquisition/document-acquisition-workbench:latest"

# Search provider (serper or brave)
search_provider = "serper"

# Job configuration
job_max_retries  = 1
job_task_timeout = 1800 # 30 minutes

# Storage
gcs_lifecycle_age_days = 7 # Shorter retention in dev

# Scheduler
enable_scheduler = false
