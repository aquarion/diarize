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

1. Install the `aws` extra (`boto3`), which the app only imports lazily
   when the `aws` backend actually runs, from the `python/` project (there's
   no Python project under `terraform/aws-transcribe/` itself):

   ```sh
   cd ../../python && uv sync --extra aws
   ```

   (or `pip install boto3`/`pip install ".[aws]"` from `python/` outside uv).
2. Attach the `iam_policy_arn` output to whatever IAM user/role you'll
   authenticate as (the app itself relies on boto3's normal credential chain
   - env vars, `~/.aws/credentials`, SSO profile, etc; it doesn't manage
   credentials).
3. Set the bucket in diarize's config (there's no installed `diarize`
   executable for the python backend - invoke `app.py` via `uv run`, as
   documented in `python/README.md`):

   ```sh
   uv run --directory ../../python app.py config set aws_s3_bucket <bucket_name output>
   uv run --directory ../../python app.py config set aws_region <aws_region>
   ```

4. Run a transcription with the `aws` engine, either as the default backend
   (`uv run --directory ../../python app.py config set backend aws`) or
   per-call (`uv run --directory ../../python app.py ... --backend aws`).

## Teardown

```sh
terraform destroy
```
