# GCS Mount Permissions Issue

## Problem Discovery

During Terraform validation, we discovered that the `mount_options` field is **not supported** in the Terraform Google provider's `google_cloud_run_v2_job` resource `gcs` block.

### Original Configuration (Invalid)
```hcl
volumes {
  name = "gcs-registry"
  gcs {
    bucket        = var.gcs_registry_bucket
    read_only     = false
    mount_options = ["uid=65534", "gid=65534", "file-mode=0644", "dir-mode=0755", "implicit-dirs"]
  }
}
```

### Current Configuration (Valid)
```hcl
volumes {
  name = "gcs-registry"
  gcs {
    bucket    = var.gcs_registry_bucket
    read_only = false
  }
}
```

## Impact

The container runs as the `nobody` user (UID 65534, GID 65534) for security. Without explicit mount options, we rely on Cloud Run v2's default gcsfuse behavior.

### Potential Issues
1. **Write permissions**: The nobody user may not have write access to the mounted GCS volume
2. **File ownership**: Created files may have incorrect ownership
3. **Directory permissions**: The application may not be able to create subdirectories

## Testing Required

Before deploying to production, test the following scenarios:

1. **Write test**: Can the application write files to `/mnt/gcs`?
   ```bash
   gcloud run jobs execute document-acquisition-job \
     --region us-central1 \
     --args="discover,--entities,/mnt/gcs/test-entities.csv,--workspace-root,/mnt/gcs"
   ```

2. **Directory creation**: Can the application create nested directories?
3. **File permissions**: Check ownership and permissions of created files in GCS

## Alternative Solutions

If write permissions fail, consider these alternatives:

### Option 1: Use GCS Client Library (Recommended)
Modify the application to use `google-cloud-storage` Python library instead of filesystem operations:
- Add `google-cloud-storage` to dependencies
- Replace `Path.write_text()` with `bucket.blob().upload_from_string()`
- Replace `Path.read_text()` with `bucket.blob().download_as_text()`

**Pros**: More reliable, better error handling, native GCS features
**Cons**: Requires code changes, breaks local development workflow

### Option 2: Run as Root User
Change Dockerfile to run as root:
```dockerfile
USER root
```

**Pros**: Simple, no code changes
**Cons**: Security risk, violates least-privilege principle

### Option 3: Use Cloud Run v2 Service Instead of Job
Services may have different gcsfuse mount behavior:
- Convert from Cloud Run Job to Cloud Run Service
- Trigger via HTTP endpoint instead of Cloud Scheduler

**Pros**: May have better mount support
**Cons**: Architectural change, different scaling model

### Option 4: Hybrid Approach
Use volume mount for reads, GCS client library for writes:
- Keep current filesystem-based reads
- Add GCS client for write operations only
- Minimal code changes

**Pros**: Balanced approach, maintains local dev workflow for reads
**Cons**: Mixed paradigm, some code changes needed

## Current Status

- ✅ Terraform validation passes
- ✅ Terraform formatting passes
- ⚠️ GCS mount write permissions **untested**
- ⚠️ End-to-end deployment **not verified**

## Recommendation

1. Deploy to dev environment first
2. Run write permission tests
3. If tests fail, implement Option 4 (Hybrid Approach) as it provides the best balance
4. Document actual behavior in deployment guide

## References

- Terraform validation error: "Blocks of type 'gcs' are not expected here" when using `mount_options`
- Cloud Run v2 uses gcsfuse for GCS volume mounts
- Default gcsfuse behavior with non-root users is unclear from documentation
