# S3 data lake (spec 01 §2). Lifecycle rules for each prefix are added with
# the issue that creates the prefix (athena-results/ = #17, raw/audio/ = #20).
variable "bucket_name" {
  type = string
}

variable "eventbridge_enabled" {
  description = "Send S3 events of this bucket to EventBridge (satellite pipeline trigger, spec 51 §2)"
  type        = bool
  default     = false
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

# One lifecycle configuration per bucket: it REPLACES every rule on the bucket,
# so add new prefixes (e.g. raw/audio/ for #20) as extra rules here.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = local.bucket_name

  # Athena query results (spec 40 §2, #17): keep 7 days.
  rule {
    id     = "athena-results-7d"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

output "bucket_name" {
  value = local.bucket_name
}

output "bucket_arn" {
  value = local.bucket_arn
}

# S3 -> EventBridge (spec 51 §2). One notification configuration per bucket: it
# REPLACES any other S3 notification on the bucket, so add future ones here too.
resource "aws_s3_bucket_notification" "eventbridge" {
  count       = var.eventbridge_enabled ? 1 : 0
  bucket      = local.bucket_name
  eventbridge = true
}
