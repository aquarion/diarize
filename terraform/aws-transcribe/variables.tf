variable "bucket_name" {
  description = "Globally-unique S3 bucket name used to stage audio uploads for AWS Transcribe jobs."
  type        = string
}

variable "aws_region" {
  description = "AWS region to create the bucket and IAM policy in."
  type        = string
}
