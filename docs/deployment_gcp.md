# GCP Production Deployment Guide (CORRECTED)

This guide shows how to deploy the document-acquisition-workbench to Google Cloud Platform (GCP) using Cloud Run Jobs for batch processing, GCS for registry storage (via Cloud Storage volume mounts), and Cloud Scheduler for periodic triggers.

## Architecture Overview

```
┌─────────────────┐
│ Cloud Scheduler │ (periodic batch triggers)
└────────┬────────┘
         │
         v (HTTP POST)
┌──────────────────┐
│ Cloud Run Job    │──────────> GCS Volume Mount (/mnt/gcs)
│ (acquisition)    │                    │
└────────┬─────────┘                    └──> GCS Bucket (registry)
         │
         ├──────────────> Secret Manager (SERPER_API_KEY or BRAVE_API_KEY)
         │
         └──────────────> Cloud Logging
```

## Key Design Decisions

- **Cloud Run Jobs** - Batch processing (not HTTP endpoint), runs to completion
- **GCS Volume Mounts** - GCS bucket mounted at `/mnt/gcs`, app writes to local paths
- **Cloud Scheduler Only** - Direct HTTP invocation (no Pub/Sub - Jobs don't support push triggers)
- **Single Secret** - Only search provider key (SERPER or BRAVE), not multiple API keys
- **Explicit Job Args** - Container requires args (no default workload)

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI installed and authenticated
- Terraform >= 1.5.0
- Docker installed locally

## Step 1: Prepare the Container Image

### 1.1 Build and Test Locally

```bash
# Build the Docker image
docker build -t document-acquisition-workbench:latest .

# Create test entities file
echo "entity_id,name,ticker,official_website,cik,country
msft,Microsoft,MSFT,microsoft.com,789019,US" > test-entities.csv

# Test locally with example data
docker run --rm -v $(pwd)/workspace:/workspace \
  -v $(pwd)/test-entities.csv:/workspace/test-entities.csv \
  document-acquisition-workbench:latest \
  discover --entities /workspace/test-entities.csv --workspace-root /workspace
```

**Note**: The CLI uses `--workspace-root`, not `--workspace`.

## Step 2: Deploy Infrastructure with Terraform

### 2.1 Create State Bucket (One-Time Setup)

Before deploying infrastructure, create the Terraform state bucket:

```bash
export PROJECT_ID="document-acquisition-dev"
export REGION="us-central1"

gcloud storage buckets create gs://${PROJECT_ID}-terraform-state \
  --project=${PROJECT_ID} \
  --location=${REGION} \
  --uniform-bucket-level-access
```

### 2.2 Directory Structure

```
terraform/
├── gcp/
│   ├── main.tf              # Root module
│   ├── variables.tf         # Input variables
│   ├── outputs.tf           # Output values
│   ├── backend-dev.hcl      # State backend config
│   └── modules/
│       ├── artifact-registry/   # Artifact Registry module
│       ├── cloud-run-job/       # Cloud Run Job module
│       ├── storage/             # GCS bucket module
│       ├── secrets/             # Secret Manager module
│       └── scheduler/           # Cloud Scheduler module
└── environments/
    ├── dev.tfvars
    ├── staging.tfvars
    └── prod.tfvars
```

### 2.3 Core Infrastructure Components

The Terraform configuration provisions:

1. **Artifact Registry** — Docker repository for container images
2. **Cloud Run Job** — Batch processing job with GCS volume mount
3. **GCS Bucket** — Registry storage (mounted at `/mnt/gcs`)
4. **Secret Manager** — One secret: `serper-api-key` or `brave-api-key`
5. **Service Account** — IAM identity with least-privilege permissions
6. **Cloud Scheduler** — Periodic batch triggers (optional, prod only)

### 2.4 Deploy Infrastructure (First Time)

For first-time deployment, follow this order to avoid image-not-found errors:

**Step 1: Bootstrap Artifact Registry**

```bash
cd terraform/gcp

# Initialize Terraform
terraform init -backend-config=backend-dev.hcl

# Create Artifact Registry only
terraform apply -var-file=../environments/dev.tfvars -target=module.artifact_registry
```

**Step 2: Build and Push Initial Image**

```bash
cd ../..  # Back to repo root

export PROJECT_ID="document-acquisition-dev"
export REGION="us-central1"

# Configure Docker authentication
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build and push image
docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/document-acquisition/document-acquisition-workbench:latest .
docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/document-acquisition/document-acquisition-workbench:latest
```

**Step 3: Deploy Full Infrastructure**

```bash
cd terraform/gcp

# Deploy everything
terraform apply -var-file=../environments/dev.tfvars
```

**Expected resources created:**
- Artifact Registry: `document-acquisition`
- Cloud Run Job: `document-acquisition-job`
- GCS bucket: `${PROJECT_ID}-document-registry`
- Secret Manager secret: `serper-api-key` or `brave-api-key`
- Service account: `document-acquisition-sa@${PROJECT_ID}.iam.gserviceaccount.com`

### 2.5 Subsequent Deployments

After the first deployment, you can run full `terraform apply` directly:

```bash
cd terraform/gcp
terraform apply -var-file=../environments/dev.tfvars
```

## Step 3: Configure Secrets

### 3.1 Store API Key

```bash
# Store Serper API key
echo -n "your-serper-api-key" | gcloud secrets versions add serper-api-key --data-file=-

# OR store Brave API key
echo -n "your-brave-api-key" | gcloud secrets versions add brave-api-key --data-file=-
```

**Note**: Only one search provider key is needed. The code reads `SERPER_API_KEY` or `BRAVE_API_KEY`.

### 3.2 Environment Variables in Cloud Run Job

The Terraform configuration automatically sets these environment variables:

```bash
DOC_WORKBENCH_HOME=/mnt/gcs
DOC_WORKBENCH_ENGINE=legacy
SERPER_API_KEY=<from Secret Manager>  # or BRAVE_API_KEY
```

## Step 4: Deploy the Application

### 4.1 Manual Deployment (for testing)

The Terraform configuration already created the Cloud Run Job. To update the image:

```bash
gcloud run jobs update document-acquisition-job \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/document-acquisition/document-acquisition-workbench:latest \
  --region=${REGION}
```

### 4.2 Execute the Job Manually

```bash
# Execute with default arguments (discover command)
gcloud run jobs execute document-acquisition-job --region=${REGION}

# Execute with custom arguments
gcloud run jobs execute document-acquisition-job \
  --region=${REGION} \
  --args="discover,--entities,/mnt/gcs/entities.csv,--workspace-root,/mnt/gcs"
```

**Note**: Use `--workspace-root`, not `--workspace`.

### 4.3 Automated Deployment (CI/CD)

See `.github/workflows/deploy-gcp.yml` for the full GitHub Actions pipeline.

**Workflow order**:
1. Bootstrap Terraform (create Artifact Registry)
2. Build and push Docker image
3. Deploy full Terraform configuration

## Step 5: GCS Registry Integration

### 5.1 Registry Structure in GCS

The workbench stores registry data in GCS via Cloud Storage volume mount:

```
gs://${PROJECT_ID}-document-registry/
├── entities.csv              # Input entities file
├── registry/
│   ├── <entity_id>_<entity_name>/
│   │   ├── documents/
│   │   │   └── *.pdf
│   │   └── metadata.json
├── runs/
│   ├── discover_20260513_120000/
│   │   ├── discover.json
│   │   └── ranking_trace.json
└── traces/
    └── <trace_id>/
        └── stage_spans.jsonl
```

### 5.2 Accessing Registry from Local CLI

The registry stores **relative paths** in manifests (e.g., `entity_123/annual_reports/2023/10-K/doc_abc/artifact.pdf`), making it fully portable between GCS and local workspaces.

```bash
# Download registry to local workspace
gsutil -m rsync -r gs://${PROJECT_ID}-document-registry/registry/ workspace/registry/

# Use locally — manifests automatically resolve relative to local registry_root
doc-workbench scan --all --workspace-root workspace/

# Upload local registry back to GCS
gsutil -m rsync -r workspace/registry/ gs://${PROJECT_ID}-document-registry/registry/
```

**Portability**: Manifests created in GCS (`/mnt/gcs/registry/...`) work seamlessly when synced to local (`workspace/registry/...`) because:
- **New manifests** store relative paths: `entity_123/annual_reports/...`
- **Old manifests** with absolute paths are automatically rebased at read time by finding the last `registry` component in the path and extracting everything after it, regardless of the full path prefix

**No migration required** — the registry handles both old absolute paths (from any environment) and new relative paths transparently. This works for manifests created in GCS (`/mnt/gcs/registry/...`), local workspaces (`/Users/.../workspace/registry/...`), or any other location, even if the absolute path contains multiple `registry` components.

## Step 6: Observability and Monitoring

### 6.1 Cloud Logging

View job execution logs:

```bash
# Stream logs from latest execution
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=document-acquisition-job" \
  --limit=100 \
  --format=json

# Query specific errors
gcloud logging read "resource.type=cloud_run_job AND severity>=ERROR" \
  --limit=50
```

### 6.2 Cloud Monitoring

Key metrics to monitor:

- **Job execution count** — `run.googleapis.com/job/completed_execution_count`
- **Job execution time** — `run.googleapis.com/job/execution_time`
- **Job failure count** — `run.googleapis.com/job/failed_execution_count`
- **GCS operations** — `storage.googleapis.com/api/request_count`

## Step 7: Cost Optimization

### 7.1 Cloud Run Jobs Pricing

- **CPU allocation**: Pay only during execution
- **Memory allocation**: Pay only during execution
- **No idle cost**: Unlike Cloud Run Service, Jobs don't incur cost when not running

**Example cost (1000 executions/month, 10 min avg, 2 vCPU, 2Gi RAM):**
- Execution time: 1000 × 10 min = 10,000 minutes = 166.67 hours
- CPU cost: 166.67 hours × 2 vCPU × $0.00002400/vCPU-second = ~$28.80
- Memory cost: 166.67 hours × 2 GiB × $0.00000250/GiB-second = ~$3.00
- **Total: ~$32/month**

### 7.2 GCS Lifecycle Policies

Automatically delete old run artifacts (configured in Terraform):

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["runs/", "traces/"]
        }
      }
    ]
  }
}
```

## Step 8: Multi-Environment Strategy

### 8.1 Environment Separation

Use separate GCP projects:

- **Dev**: `document-acquisition-dev`
- **Staging**: `document-acquisition-staging`
- **Prod**: `document-acquisition-prod`

### 8.2 Environment-Specific Configs

`environments/dev.tfvars`:
```hcl
project_id = "document-acquisition-dev"
region = "us-central1"
environment = "dev"
search_provider = "serper"
job_max_retries = 1
job_task_timeout = 1800
gcs_lifecycle_age_days = 7
enable_scheduler = false
```

`environments/prod.tfvars`:
```hcl
project_id = "document-acquisition-prod"
region = "us-central1"
environment = "prod"
search_provider = "serper"
job_max_retries = 3
job_task_timeout = 3600
gcs_lifecycle_age_days = 30
enable_scheduler = true
scheduler_cron = "0 2 * * *"  # Daily at 2 AM
```

## Step 9: Security Hardening

### 9.1 Cloud Storage Volume Mount

The workbench uses Cloud Storage volume mounts for secure GCS access:

- GCS bucket mounted at `/mnt/gcs` inside container
- App writes to local paths (e.g., `/mnt/gcs/registry/`)
- Changes automatically sync to GCS
- No GCS client library needed in application code

**Mount Permissions**:

The GCS volume mount uses Cloud Run v2's default gcsfuse configuration. The Terraform Google provider does not support custom mount options in the `gcs` block.

⚠️ **Important**: Write permissions for the `nobody` user (UID 65534) should be tested in the dev environment before production deployment. If write permission issues occur, see `terraform/GCS_MOUNT_PERMISSIONS_ISSUE.md` for alternative solutions including using the GCS client library.

### 9.2 Non-Root Container

The Dockerfile runs as a non-root user:

```dockerfile
USER nobody
```

Code area is read-only; only `/workspace` (mapped to `/mnt/gcs`) is writable.

## Step 10: Troubleshooting

### Issue: Job fails with "Permission denied" on GCS

**Cause:** Service account lacks `storage.objectAdmin` role.

**Fix:**
```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:document-acquisition-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

### Issue: Job times out after 30 minutes

**Cause:** Default task timeout is too short for large batches.

**Fix:** Update `job_task_timeout` in tfvars and re-apply Terraform.

### Issue: Job exits with "No such option: --workspace"

**Cause:** Using wrong CLI option.

**Fix:** Use `--workspace-root` instead of `--workspace`.

## Cost Estimation

**Monthly cost for dev environment (100 executions/month):**

| Service | Configuration | Estimated Cost |
|---------|--------------|----------------|
| Cloud Run Jobs | 100 executions, 10 min avg, 2 vCPU, 2Gi RAM | $3.20 |
| GCS | 10GB storage, 10K operations/month | $0.30 |
| Secret Manager | 1 secret, 1K accesses/month | $0.06 |
| Cloud Logging | 2GB logs/month | $1.00 |
| Artifact Registry | 10GB storage | $0.10 |
| **Total** | | **~$4.66/month** |

**Monthly cost for prod environment (1000 executions/month):**

| Service | Configuration | Estimated Cost |
|---------|--------------|----------------|
| Cloud Run Jobs | 1000 executions, 10 min avg, 2 vCPU, 2Gi RAM | $32.00 |
| GCS | 100GB storage, 100K operations/month | $3.50 |
| Secret Manager | 1 secret, 10K accesses/month | $0.06 |
| Cloud Logging | 20GB logs/month | $10.00 |
| Artifact Registry | 50GB storage | $0.50 |
| **Total** | | **~$46.06/month** |

## Next Steps

1. **Review Terraform modules** in `terraform/gcp/modules/`
2. **Set up CI/CD pipeline** with `.github/workflows/deploy-gcp.yml`
3. **Configure monitoring alerts** in Cloud Monitoring
4. **Test batch processing** with sample entity lists
5. **Document runbooks** for common operational tasks

## References

- [Cloud Run Jobs Documentation](https://cloud.google.com/run/docs/create-jobs)
- [Cloud Run Job Execution](https://cloud.google.com/run/docs/execute/jobs)
- [Cloud Storage Volume Mounts for Cloud Run Jobs](https://cloud.google.com/run/docs/configuring/jobs/cloud-storage-volume-mounts)
- [Cloud Run Scheduled Job Execution](https://cloud.google.com/run/docs/execute/jobs-on-schedule)
- [Cloud Storage Documentation](https://cloud.google.com/storage/docs)
- [Secret Manager Best Practices](https://cloud.google.com/secret-manager/docs/best-practices)
- [Terraform GCP Provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs)
