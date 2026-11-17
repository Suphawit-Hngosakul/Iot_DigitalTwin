# S3 data lake (spec 01 §2). Lifecycle for raw/audio/ and athena-results/
# is added with the issues that create those prefixes.
variable "bucket_name" {
  type = string
}

# Learner Lab: the bucket is created OUTSIDE Terraform (aws s3 mb, see
# cloud/infra/README.md). The aws_s3_bucket resource always reads the bucket's
# Object Lock configuration, and the Learner Lab SCP denies
# s3:GetBucketObjectLockConfiguration (403) -> apply fails. So Terraform only
# manages the settings below, on a bucket that already exists.
locals {
  bucket_name = var.bucket_name
  bucket_arn  = "arn:aws:s3:::${var.bucket_name}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = local.bucket_name
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = local.bucket_name
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = local.bucket_name
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

output "bucket_name" {
  value = local.bucket_name
}

output "bucket_arn" {
  value = local.bucket_arn
}
