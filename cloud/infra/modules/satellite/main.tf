# GEE connectivity test Lambda (issue #25, spec 50).
# Auth: Workload Identity Federation, the Lambda's LabRole credentials are
# exchanged for a Google token, so no service-account key is stored anywhere.
terraform {
  required_providers {
    aws     = { source = "hashicorp/aws" }
    archive = { source = "hashicorp/archive" }
  }
}

variable "name_prefix" {
  type = string
}

variable "role_arn" {
  type = string
}

variable "handler_file" {
  description = "Path to handler.py"
  type        = string
}

variable "layer_zip" {
  description = "Path to gee-layer.zip (python/ folder, built for python3.12 x86_64)"
  type        = string
}

variable "gee_project" {
  type = string
}

variable "gee_assets" {
  type = string
}

variable "gee_wif" {
  type = object({
    project_number  = string
    pool_id         = string
    provider_id     = string
    service_account = string
  })
}

locals {
  function_name = "${var.name_prefix}-satellite-test"

  # Same shape as `gcloud iam workload-identity-pools create-cred-config --aws`.
  wif_config = jsonencode({
    universe_domain    = "googleapis.com"
    type               = "external_account"
    audience           = "//iam.googleapis.com/projects/${var.gee_wif.project_number}/locations/global/workloadIdentityPools/${var.gee_wif.pool_id}/providers/${var.gee_wif.provider_id}"
    subject_token_type = "urn:ietf:params:aws:token-type:aws4_request"
    token_url          = "https://sts.googleapis.com/v1/token"
    credential_source = {
      environment_id                 = "aws1"
      region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
      url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
    }
    service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${var.gee_wif.service_account}:generateAccessToken"
  })
}

# earthengine-api + google-auth, shared later with satellite_ingest (#27).
resource "aws_lambda_layer_version" "gee" {
  layer_name               = "${var.name_prefix}-gee"
  filename                 = var.layer_zip
  source_code_hash         = filebase64sha256(var.layer_zip)
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "earthengine-api + google-auth (cloud/satellite/layer/requirements.txt)"
}

data "archive_file" "satellite" {
  type        = "zip"
  output_path = "${path.module}/build/satellite_test.zip"
  source_file = var.handler_file
}

resource "aws_cloudwatch_log_group" "satellite" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "satellite" {
  function_name    = local.function_name
  role             = var.role_arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.satellite.output_path
  source_code_hash = data.archive_file.satellite.output_base64sha256
  memory_size      = 512
  timeout          = 60
  layers           = [aws_lambda_layer_version.gee.arn]

  environment {
    variables = {
      GEE_PROJECT    = var.gee_project
      GEE_ASSETS     = var.gee_assets
      GEE_WIF_CONFIG = local.wif_config
    }
  }

  depends_on = [aws_cloudwatch_log_group.satellite]
}

output "function_name" {
  value = aws_lambda_function.satellite.function_name
}

output "layer_arn" {
  value = aws_lambda_layer_version.gee.arn
}
