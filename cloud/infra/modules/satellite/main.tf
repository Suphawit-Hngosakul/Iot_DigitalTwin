# GEE Lambdas (spec 50): connectivity test (#25) + satellite_ingest (#27).
# Auth: Workload Identity Federation, the Lambda's own role credentials are
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

variable "layer_build_script" {
  description = "build_layer.py. Set = Terraform builds layer_zip itself on apply (rebuilds when requirements.txt changes). Empty = layer_zip is prebuilt."
  type        = string
  default     = ""
}

variable "python_cmd" {
  description = "Python with pip that runs layer_build_script (python on Windows, python3 on most Linux/macOS)"
  type        = string
  default     = "python"
}

variable "gee_project" {
  type = string
}

variable "gee_assets" {
  type = string
}

variable "ingest_source_dir" {
  description = "cloud/satellite/ingest (handler.py + ingest.py)"
  type        = string
}

variable "data_bucket" {
  description = "Data lake bucket for raw/satellite/ (same bucket as IoT)"
  type        = string
}

variable "ingest_schedule" {
  description = "EventBridge schedule for satellite_ingest. Empty = manual runs only."
  type        = string
}

variable "ingest_max_cloud_pct" {
  description = "Skip a scene date when the cloud share inside the AOI is above this (spec 50 §5)"
  type        = number
}

variable "ingest_min_valid_ratio" {
  description = "Skip a scene date when fewer than this share of AOI pixels are clear"
  type        = number
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

locals {
  build_layer        = var.layer_build_script != ""
  layer_requirements = "${dirname(var.layer_build_script)}/requirements.txt"
  # Auto-build: hash the inputs (known at plan time, zip may not exist yet).
  # Prebuilt: hash the zip itself.
  layer_hash = local.build_layer ? base64sha256(join("", [
    filesha256(local.layer_requirements), filesha256(var.layer_build_script),
  ])) : filebase64sha256(var.layer_zip)
}

# pip-downloads manylinux wheels into layer_zip. Runs on first apply and
# whenever requirements.txt or build_layer.py changes, not on every apply.
resource "terraform_data" "build_layer" {
  count            = local.build_layer ? 1 : 0
  triggers_replace = [local.layer_hash]

  # No shell: Terraform runs [python_cmd, <abs path>] directly, so the path needs
  # no quoting (cmd.exe on Windows mangled "./../satellite/..." into a bad path).
  provisioner "local-exec" {
    interpreter = [var.python_cmd]
    command     = abspath(var.layer_build_script)
  }
}

# earthengine-api + google-auth, shared with satellite_ingest (#27).
resource "aws_lambda_layer_version" "gee" {
  layer_name               = "${var.name_prefix}-gee"
  filename                 = var.layer_zip
  source_code_hash         = local.layer_hash
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "earthengine-api + google-auth (cloud/satellite/layer/requirements.txt)"

  depends_on = [terraform_data.build_layer]
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

# ---------------------------------------------------------------------------
# satellite_ingest (#27): Landsat 8/9 -> S3 raw/satellite/ (spec 50 §3-4)
# ---------------------------------------------------------------------------
locals {
  ingest_function_name = "${var.name_prefix}-satellite-ingest"
}

data "archive_file" "ingest" {
  type        = "zip"
  output_path = "${path.module}/build/satellite_ingest.zip"
  source_dir  = var.ingest_source_dir
  # Only handler.py + ingest.py run in Lambda; GEE Code Editor scripts and tests stay out.
  excludes = ["__pycache__", "__pycache__/*", "*.pyc", "*.js", "tests", "tests/*", "requirements-dev.txt"]
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.ingest_function_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "ingest" {
  function_name    = local.ingest_function_name
  role             = var.role_arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  memory_size      = 1024
  timeout          = 900 # Lambda max; a backfill must be split into ranges of 1-2 months (see cloud/satellite/README.md)
  layers           = [aws_lambda_layer_version.gee.arn]

  environment {
    variables = {
      GEE_PROJECT     = var.gee_project
      GEE_ASSETS      = var.gee_assets
      GEE_WIF_CONFIG  = local.wif_config
      DATA_BUCKET     = var.data_bucket
      MAX_CLOUD_PCT   = tostring(var.ingest_max_cloud_pct)
      MIN_VALID_RATIO = tostring(var.ingest_min_valid_ratio)
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest]
}

# No automatic retry of async invokes (EventBridge / --invocation-type Event):
# a failed backfill should not run twice in parallel. Ingest is idempotent, re-run by hand.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = 0
}

# Daily (spec 50 §3): Landsat 8+9 revisit ~8 days, so most runs find nothing new.
resource "aws_cloudwatch_event_rule" "ingest" {
  count               = var.ingest_schedule == "" ? 0 : 1
  name                = "${local.ingest_function_name}-schedule"
  description         = "Look for new Landsat 8/9 scenes over the AOI (#27)"
  schedule_expression = var.ingest_schedule
}

resource "aws_cloudwatch_event_target" "ingest" {
  count     = var.ingest_schedule == "" ? 0 : 1
  rule      = aws_cloudwatch_event_rule.ingest[0].name
  target_id = "satellite-ingest"
  arn       = aws_lambda_function.ingest.arn
  input     = jsonencode({ mode = "ingest" })
}

resource "aws_lambda_permission" "ingest_events" {
  count         = var.ingest_schedule == "" ? 0 : 1
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest[0].arn
}

output "ingest_function_name" {
  value = aws_lambda_function.ingest.function_name
}
