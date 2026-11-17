# Lambda satellite GEE test & ingest (issue #25, #27, spec 50).
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

variable "source_dir" {
  description = "Directory containing handler.py and gee-wif-config.json"
  type        = string
}

variable "layer_zip" {
  description = "Path to gee-layer.zip"
  type        = string
}

variable "gee_project" {
  description = "Google Earth Engine project ID"
  type        = string
  default     = "turnkey-tribute-511903-t6"
}

variable "gee_assets" {
  description = "Asset path to verify in GEE"
  type        = string
  default     = "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
}

locals {
  function_name = "${var.name_prefix}-satellite-test"
}

# 1. GEE Lambda Layer (earthengine-api, google-auth for python3.12 x86_64)
resource "aws_lambda_layer_version" "gee" {
  layer_name               = "${var.name_prefix}-gee-layer"
  filename                 = var.layer_zip
  source_code_hash         = filebase64sha256(var.layer_zip)
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "Google Earth Engine and Google Auth WIF dependencies"
}

# 2. Package Lambda code (handler.py + ingest.py + gee-wif-config.json)
data "archive_file" "satellite" {
  type        = "zip"
  output_path = "${path.module}/build/satellite_test.zip"
  source_dir  = var.source_dir
}

# 3. CloudWatch Log Group
resource "aws_cloudwatch_log_group" "satellite" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

# 4. Lambda Function
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

  layers = [aws_lambda_layer_version.gee.arn]

  environment {
    variables = {
      GEE_PROJECT = var.gee_project
      GEE_ASSETS  = var.gee_assets
    }
  }

  depends_on = [aws_cloudwatch_log_group.satellite]
}

output "function_name" {
  description = "Name of the GEE test Lambda function"
  value       = aws_lambda_function.satellite.function_name
}

output "function_arn" {
  description = "ARN of the GEE test Lambda function"
  value       = aws_lambda_function.satellite.arn
}

output "layer_arn" {
  description = "ARN of the GEE Lambda Layer"
  value       = aws_lambda_layer_version.gee.arn
}
