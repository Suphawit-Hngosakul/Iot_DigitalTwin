# Lambda api + its own HTTP API: GET /devices /latest /readings /quality (spec 40 §3, #18)
# + /layers /grid /grid/{cell_id} (#34).
# Separate from the ingest API so CORS / throttling for the web never touch device uploads.
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
variable "athena_database" { type = string }
variable "athena_workgroup" { type = string }
variable "athena_output" { type = string }

variable "allowed_origins" {
  description = "CORS origins of the web. [\"*\"] until the web (#35) has a domain."
  type        = list(string)
  default     = ["*"]
}

variable "throttle_rate" {
  description = "Requests/s for the whole API (each miss can start an Athena query)"
  type        = number
  default     = 20
}

locals {
  function_name = "${var.name_prefix}-api"
  routes        = ["/devices", "/latest", "/readings", "/quality", "/layers", "/grid", "/grid/{cell_id}"]
}

# handler.py + common/{schema,grid,satellite_schema,curated}.py + devices.csv (stdlib + boto3 only, no layer).
data "archive_file" "api" {
  type        = "zip"
  output_path = "${path.module}/build/api.zip"

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
  source {
    content  = file("${var.common_dir}/grid.py")
    filename = "common/grid.py"
  }
  source {
    content  = file("${var.common_dir}/satellite_schema.py")
    filename = "common/satellite_schema.py"
  }
  source {
    content  = file("${var.common_dir}/curated.py")
    filename = "common/curated.py"
  }
  source {
    content  = file("${var.common_dir}/devices.csv")
    filename = "common/devices.csv"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "api" {
  function_name    = local.function_name
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  memory_size      = 512
  timeout          = 29 # API GW waits at most 30 s; Athena gets 25 s of it

  environment {
    variables = {
      DATA_BUCKET      = var.bucket_name
      ATHENA_DB        = var.athena_database
      ATHENA_WORKGROUP = var.athena_workgroup
      ATHENA_OUTPUT    = var.athena_output
      ATHENA_TIMEOUT_S = "25"
      CACHE_TTL_S      = "60"
      LAYER_URL_TTL_S  = "3600" # presigned GET for the layer PNGs (#34)
      ALLOWED_ORIGINS  = join(",", var.allowed_origins) # informational; CORS is done by API GW
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
}

resource "aws_apigatewayv2_api" "api" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "api" {
  for_each  = toset(local.routes)
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET ${each.value}"
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
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
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/GET/*"
}

output "api_url" {
  value = aws_apigatewayv2_api.api.api_endpoint
}

output "function_name" {
  value = aws_lambda_function.api.function_name
}
