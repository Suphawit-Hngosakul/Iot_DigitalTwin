# Web hosting (plan R4): private S3 bucket + CloudFront (OAC), HTTPS on the
# default *.cloudfront.net certificate, no domain needed.
# Second origin: the realtime hub on the relay EC2, so the page (https) can open
# wss://<cloudfront>/live instead of ws://<eip>:8081/live (mixed content is blocked).
# config.json is written by Terraform; the Vite build in dist_dir is uploaded as is.
variable "name_prefix" { type = string }

variable "dist_dir" {
  description = "Vite build output (run npm run build in web/ before apply). Missing/empty = upload nothing."
  type        = string
}

variable "api_url" { type = string }

variable "hub_origin_domain" {
  description = "Public DNS name of the relay EC2 (CloudFront needs a name, not an IP). Empty = no /live route."
  type        = string
  default     = ""
}

variable "hub_port" {
  type    = number
  default = 8081
}

variable "price_class" {
  description = "PriceClass_200 includes edge locations in Asia (Bangkok/Singapore)"
  type        = string
  default     = "PriceClass_200"
}

data "aws_caller_identity" "this" {}

locals {
  bucket_name = "${var.name_prefix}-web-${data.aws_caller_identity.this.account_id}"
  hub_enabled = var.hub_origin_domain != ""

  # AWS managed policies (ids are global constants)
  cache_optimized   = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
  cache_disabled    = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
  origin_all_viewer = "216adef6-5c7f-47e4-b989-5492eafa07d3" # Managed-AllViewer (WebSocket headers + query)

  mime = {
    html    = "text/html; charset=utf-8"
    js      = "text/javascript; charset=utf-8"
    mjs     = "text/javascript; charset=utf-8"
    css     = "text/css; charset=utf-8"
    json    = "application/json"
    geojson = "application/geo+json"
    svg     = "image/svg+xml"
    png     = "image/png"
    jpg     = "image/jpeg"
    jpeg    = "image/jpeg"
    webp    = "image/webp"
    ico     = "image/x-icon"
    woff    = "font/woff"
    woff2   = "font/woff2"
    txt     = "text/plain; charset=utf-8"
    map     = "application/json"
    pbf     = "application/x-protobuf"
  }

  # config.json is managed below, never taken from dist/.
  # data/buildings.geojson (web/scripts/convert_buildings.py) may be public and is uploaded if present.
  dist_files = [for f in try(fileset(var.dist_dir, "**"), []) : f if f != "config.json"]
}

# ---- bucket (private, only CloudFront reads it) ------------------------------

resource "aws_s3_bucket" "web" {
  bucket        = local.bucket_name
  force_destroy = true # short-lived demo: terraform destroy empties it
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---- CloudFront ---------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.name_prefix}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "${var.name_prefix} web"
  default_root_object = "index.html"
  price_class         = var.price_class
  http_version        = "http2"

  origin {
    origin_id                = "s3-web"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  dynamic "origin" {
    for_each = local.hub_enabled ? [1] : []
    content {
      origin_id   = "hub"
      domain_name = var.hub_origin_domain
      custom_origin_config {
        http_port              = var.hub_port
        https_port             = 443
        origin_protocol_policy = "http-only" # hub has no TLS (D22); CloudFront terminates HTTPS
        origin_ssl_protocols   = ["TLSv1.2"]
        origin_read_timeout    = 60
      }
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = local.cache_optimized
  }

  # config.json changes on every apply -> never cached at the edge
  ordered_cache_behavior {
    path_pattern           = "/config.json"
    target_origin_id       = "s3-web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = local.cache_disabled
  }

  # wss://<cloudfront>/live -> ws://<relay>:8081/live (spec 22)
  dynamic "ordered_cache_behavior" {
    for_each = local.hub_enabled ? [1] : []
    content {
      path_pattern             = "/live*"
      target_origin_id         = "hub"
      viewer_protocol_policy   = "https-only"
      allowed_methods          = ["GET", "HEAD", "OPTIONS"]
      cached_methods           = ["GET", "HEAD"]
      cache_policy_id          = local.cache_disabled
      origin_request_policy_id = local.origin_all_viewer
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "web" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  policy     = data.aws_iam_policy_document.web.json
  depends_on = [aws_s3_bucket_public_access_block.web]
}

# ---- content ------------------------------------------------------------------

locals {
  hub_live_url = local.hub_enabled ? "wss://${aws_cloudfront_distribution.web.domain_name}/live" : ""
  web_config = {
    api_url      = var.api_url
    hub_live_url = local.hub_live_url
    # changes only when the build or the endpoints change (no perpetual diff)
    deploy_id = substr(sha1(join(",", concat(
      [var.api_url, local.hub_live_url],
      [for f in local.dist_files : filemd5("${var.dist_dir}/${f}")],
    ))), 0, 12)
  }
}

resource "aws_s3_object" "dist" {
  for_each     = toset(local.dist_files)
  bucket       = aws_s3_bucket.web.id
  key          = each.value
  source       = "${var.dist_dir}/${each.value}"
  etag         = filemd5("${var.dist_dir}/${each.value}")
  content_type = lookup(local.mime, lower(reverse(split(".", each.value))[0]), "application/octet-stream")
  # only Vite's hashed assets/ are immutable; data/*, index.html etc. keep their name -> revalidate
  cache_control = startswith(each.value, "assets/") ? "public, max-age=31536000, immutable" : "no-cache"
}

resource "aws_s3_object" "config" {
  bucket        = aws_s3_bucket.web.id
  key           = "config.json"
  content       = jsonencode(local.web_config)
  content_type  = "application/json"
  cache_control = "no-store, no-cache, must-revalidate"
}

# ---- outputs ------------------------------------------------------------------

output "web_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "hub_live_url" {
  description = "wss URL the page uses (through CloudFront); empty when the hub is off"
  value       = local.hub_live_url
}

output "bucket_name" {
  value = aws_s3_bucket.web.bucket
}

output "distribution_id" {
  description = "For aws cloudfront create-invalidation after re-uploading index.html"
  value       = aws_cloudfront_distribution.web.id
}
