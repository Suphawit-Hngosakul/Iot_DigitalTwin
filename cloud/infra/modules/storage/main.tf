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

variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "cors_allowed_origins" {
  description = "Origins allowed to GET objects with a presigned URL from the browser (layer PNGs of /layers, #34). [] = no CORS rule."
  type        = list(string)
  default     = []
}

# The bucket is created with the AWS CLI from Terraform (needs `aws` + `bash` on
# PATH) and Terraform manages only the settings below. Kept from the Learner Lab
# days, whose SCP denied s3:GetBucketObjectLockConfiguration that the
# aws_s3_bucket resource always reads (c3d9ba3); it also works on any account. Idempotent: an existing bucket is left as it is.
# `terraform destroy` does NOT delete the bucket or its data (delete it by hand).
resource "terraform_data" "bucket" {
  input = var.bucket_name

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      if aws s3api head-bucket --bucket "${var.bucket_name}" 2>/dev/null; then
        echo "bucket ${var.bucket_name} exists"
      elif [ "${var.region}" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "${var.bucket_name}" --region us-east-1
      else
        aws s3api create-bucket --bucket "${var.bucket_name}" --region "${var.region}" \
          --create-bucket-configuration LocationConstraint="${var.region}"
      fi
    EOT
  }
}

locals {
  # .output (not var.bucket_name) so every resource that uses the bucket waits for it
  bucket_name = terraform_data.bucket.output
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

# CORS for the web (#34 / #36): MapLibre loads layer PNGs with fetch(), so the
# presigned GET from /layers needs Access-Control-Allow-Origin. The bucket stays
# private (public access block above); this only lets a browser read a response
# it already has a valid presigned URL for.
resource "aws_s3_bucket_cors_configuration" "data" {
  count  = length(var.cors_allowed_origins) > 0 ? 1 : 0
  bucket = local.bucket_name

  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = var.cors_allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3600
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
