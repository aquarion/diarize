# aws-transcribe

Provisions the S3 bucket the `aws` transcription backend (`python/transcribe.py:run_aws_transcribe_pipeline`)
uploads audio to, plus a least-privilege IAM policy scoped to that bucket and
AWS Transcribe. Run once by hand - the app never invokes Terraform itself.

## Usage

```sh
cd terraform/aws-transcribe
cp terraform.tfvars.example terraform.tfvars   # edit bucket_name / aws_region
terraform init
terraform apply
```

State is kept local (gitignored) - this is a single-user, run-once module,
not a shared/CI-managed one.

## After applying

1. Attach the `iam_policy_arn` output to whatever IAM user/role you'll
   authenticate as (the app itself relies on boto3's normal credential chain
   - env vars, `~/.aws/credentials`, SSO profile, etc; it doesn't manage
   credentials).
2. Set the bucket in diarize's config:

   ```sh
   diarize config set aws_s3_bucket <bucket_name output>
   diarize config set aws_region <aws_region>
   ```

3. Run a transcription with the `aws` engine, either as the default backend
   (`diarize config set backend aws`) or per-call (`--backend aws`).

## Teardown

```sh
terraform destroy
```
