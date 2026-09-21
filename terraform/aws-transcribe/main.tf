terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # No backend block: state is kept local (gitignored) - this module is
  # meant to be run once by hand, not as part of a team/CI workflow.
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "audio_staging" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "audio_staging" {
  bucket = aws_s3_bucket.audio_staging.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "audio_staging" {
  bucket = aws_s3_bucket.audio_staging.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter {} # applies to every object in the bucket

    # Safety net alongside the app's own post-job delete_object call - in
    # case a crashed job leaves an upload behind.
    expiration {
      days = 1
    }
  }
}

data "aws_iam_policy_document" "diarize_aws_transcribe" {
  statement {
    sid    = "AudioUpload"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.audio_staging.arn}/*"]
  }

  statement {
    sid    = "TranscribeJobs"
    effect = "Allow"
    actions = [
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
    ]
    # Transcribe jobs aren't addressable by ARN at creation time.
    resources = ["*"]
  }
}

resource "aws_iam_policy" "diarize_aws_transcribe" {
  name        = "diarize-aws-transcribe-${var.bucket_name}"
  description = "Least-privilege access for the diarize project's AWS Transcribe backend."
  policy      = data.aws_iam_policy_document.diarize_aws_transcribe.json
}
