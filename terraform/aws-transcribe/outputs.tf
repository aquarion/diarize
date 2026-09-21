output "bucket_name" {
  description = "Paste into: diarize config set aws_s3_bucket <value>"
  value       = aws_s3_bucket.audio_staging.bucket
}

output "iam_policy_arn" {
  description = "Attach this policy to the IAM user/role diarize will authenticate as."
  value       = aws_iam_policy.diarize_aws_transcribe.arn
}
