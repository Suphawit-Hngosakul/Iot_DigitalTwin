# Lambda ingest + HTTP API POST /ingest (spec 20).
terraform {
  required_providers {
    aws     = { source = "hashicorp/aws" }
    archive = { source = "hashicorp/archive" }
  }
}

variable "name_prefix" { type = string }
variable "role_arn" { type = string }
variable "bucket_name" { type = string }
variable "source_dir" { type = string }
variable "common_dir" { type = string }
variable "device_token_hashes" {
  type      = map(string)
  sensitive = true
}

variable "throttle_rate" {
  description = "Requests/s for the whole API (devices send one chunk at a time)"
  type        = number
  default     = 10
}

locals {
  function_name = "${var.name_prefix}-ingest"
}

# Zip handler.py + common/schema.py (stdlib only, no layer needed).
data "archive_file" "ingest" {
  type        = "zip"
  output_path = "${path.module}/build/ingest.zip"

  source {
    content  = file("${var.source_dir}/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${var.common_dir}/__init__.py")
    filename = "common/__init__.py"
  }
  source {
    content  = file("${var.common_dir}/schema.py")
    filename = "common/schema.py"
  }
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "ingest" {
  function_name    = local.function_name
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  memory_size      = 256
  timeout          = 10

  environment {
    variables = {
      DATA_BUCKET     = var.bucket_name
      DEVICE_TOKENS   = jsonencode(var.device_token_hashes)
      TZ_OFFSET_HOURS = "7"
      MAX_BODY_BYTES  = "1048576"
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest]
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "ingest" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingest.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "ingest" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /ingest"
  target    = "integrations/${aws_apigatewayv2_integration.ingest.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = var.throttle_rate * 2
    throttling_rate_limit  = var.throttle_rate
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*/ingest"
}

output "api_id" {
  value = aws_apigatewayv2_api.http.id
}

output "api_endpoint" {
  value = aws_apigatewayv2_api.http.api_endpoint
}

output "ingest_url" {
  value = "${aws_apigatewayv2_api.http.api_endpoint}/ingest"
}
