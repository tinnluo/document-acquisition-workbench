# Terraform Validation Instructions

This document provides instructions for validating the Terraform configuration after the initial setup, since Terraform is not installed in the current development environment.

## Prerequisites

Install Terraform >= 1.5.0:
```bash
# macOS
brew install terraform

# Linux
wget https://releases.hashicorp.com/terraform/1.5.0/terraform_1.5.0_linux_amd64.zip
unzip terraform_1.5.0_linux_amd64.zip
sudo mv terraform /usr/local/bin/
```

## Validation Steps

### 1. Initialize Terraform (generates .terraform.lock.hcl)

```bash
cd terraform/gcp
terraform init -backend-config=backend-dev.hcl
```

This will:
- Download required providers (Google, Random)
- Generate `.terraform.lock.hcl` with pinned provider versions
- Initialize the backend configuration

### 2. Validate Configuration

```bash
terraform validate
```

Expected output:
```
Success! The configuration is valid.
```

### 3. Format Check

```bash
terraform fmt -check -recursive
```

Expected output: (no output means all files are properly formatted)

If formatting issues are found:
```bash
terraform fmt -recursive
```

### 4. Plan (Dry Run)

```bash
# Dev environment
terraform plan -var-file=../environments/dev.tfvars

# Staging environment
terraform plan -var-file=../environments/staging.tfvars

# Production environment
terraform plan -var-file=../environments/prod.tfvars
```

This validates that:
- All variables are properly defined
- Module references are correct
- Resource configurations are valid
- No circular dependencies exist

### 5. Commit .terraform.lock.hcl

After successful `terraform init`, commit the lock file:

```bash
git add terraform/gcp/.terraform.lock.hcl
git commit -m "chore: add Terraform provider lock file"
```

## Expected Provider Versions

Based on the configuration, the lock file should pin:

- `hashicorp/google` >= 5.0.0
- `hashicorp/random` >= 3.5.0

## Troubleshooting

### Backend Bucket Doesn't Exist

If `terraform init` fails because the backend bucket doesn't exist:

```bash
# Create the state bucket first
gsutil mb -p ${PROJECT_ID} -l ${REGION} gs://${PROJECT_ID}-terraform-state
gsutil versioning set on gs://${PROJECT_ID}-terraform-state

# Then retry init
terraform init -backend-config=backend-dev.hcl
```

### Module Not Found Errors

Ensure you're in the correct directory:
```bash
cd terraform/gcp  # Root module directory
terraform init
```

### Variable Validation Errors

Check that your `.tfvars` file includes all required variables:
- `project_id`
- `region`
- `environment`
- `docker_image_tag`

## CI/CD Validation

The GitHub Actions workflow (`.github/workflows/deploy-gcp.yml`) automatically runs:
- `terraform fmt -check`
- `terraform validate`
- `terraform plan`

Before each deployment, ensuring configuration validity.
