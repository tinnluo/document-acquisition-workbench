project_id  = "document-acquisition-prod"
region      = "us-central1"
environment = "prod"

# Image URI (updated by CI/CD)
image_uri = "us-central1-docker.pkg.dev/document-acquisition-prod/document-acquisition/document-acquisition-workbench:latest"

# Search provider (serper or brave)
search_provider = "serper"

# Job configuration
job_max_retries  = 3
job_task_timeout = 3600 # 60 minutes

# Storage
gcs_lifecycle_age_days = 30

# Scheduler
enable_scheduler = true
scheduler_cron   = "0 2 * * *" # Daily at 2 AM
