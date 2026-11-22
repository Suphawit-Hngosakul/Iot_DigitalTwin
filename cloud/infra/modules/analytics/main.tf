# Heat analytics pipeline (Spec 60 §3-4, Issue #33):
# Triggered automatically when curated-merge publishes curated/_success/merge/<period>.json.
terraform {
  required_providers {
    aws     = { source = "hashicorp/aws" }
    archive = { source = "hashicorp/archive" }
  }
}

variable "name_prefix" { type = string }
variable "role_arn" { type = string }
variable "bucket_name" { type = string }
variable "functions_dir" { type = string }
variable "stats_dir" { type = string }
variable "common_dir" { type = string }
variable "statemachine_file" { type = string }
variable "athena_database" { type = string }
variable "athena_workgroup" { type = string }
variable "pandas_layer_arn" { type = string }

variable "alert_topic_arn" {
  description = "SNS topic for PipelineFailed"
  type        = string
}

variable "trigger_enabled" {
  description = "Run analytics automatically when curated-merge publishes a period"
  type        = bool
  default     = true
}

variable "weekly_schedule" {
  description = "Weekly EventBridge Scheduler expression (Asia/Bangkok): recompute every merged period (mode=force)"
  type        = string
  default     = "cron(0 11 ? * SUN *)"
}

locals {
  common_files = ["__init__.py", "schema.py", "curated.py", "satellite_schema.py", "grid.py", "devices.csv"]
  stats_files  = ["__init__.py", "core.py", "runner.py", "storage.py"]

  functions = {
    plan_analytics = { timeout = 120, memory = 256, use_pandas = false }
    run_analytics  = { timeout = 600, memory = 1024, use_pandas = true }
  }
}

data "archive_file" "fn" {
  for_each    = local.functions
  type        = "zip"
  output_path = "${path.module}/build/${each.key}.zip"

  source {
    content  = file("${var.functions_dir}/${each.key}/handler.py")
    filename = "handler.py"
  }

  dynamic "source" {
    for_each = local.common_files
    content {
      content  = file("${var.common_dir}/${source.value}")
      filename = "common/${source.value}"
    }
  }

  dynamic "source" {
    for_each = local.stats_files
    content {
      content  = file("${var.stats_dir}/${source.value}")
      filename = "stats/${source.value}"
    }
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name_prefix}-analytics-${replace(each.key, "_", "-")}"
  retention_in_days = 14
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-analytics-${replace(each.key, "_", "-")}"
  role             = var.role_arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.handler"
  filename         = data.archive_file.fn[each.key].output_path
  source_code_hash = data.archive_file.fn[each.key].output_base64sha256
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  layers           = each.value.use_pandas ? [var.pandas_layer_arn] : []

  environment {
    variables = {
      DATA_BUCKET      = var.bucket_name
      ATHENA_DATABASE  = var.athena_database
      ATHENA_WORKGROUP = var.athena_workgroup
    }
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_sfn_state_machine" "analytics" {
  name     = "${var.name_prefix}-analytics"
  role_arn = var.role_arn
  type     = "STANDARD"
  definition = templatefile(var.statemachine_file, {
    plan_analytics_arn = aws_lambda_function.fn["plan_analytics"].arn
    run_analytics_arn  = aws_lambda_function.fn["run_analytics"].arn
    alert_topic_arn    = var.alert_topic_arn
  })
}

# Triggered automatically when curated-merge writes curated/_success/merge/<period>.json
resource "aws_cloudwatch_event_rule" "merge_published" {
  name        = "${var.name_prefix}-analytics-merge-published"
  description = "curated-merge published -> analytics S1-S8 (Spec 60 §4, Issue #33)"
  state       = var.trigger_enabled ? "ENABLED" : "DISABLED"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.bucket_name] }
      object = { key = [
        { wildcard = "curated/_success/merge/*.json" },
      ] }
    }
  })
}

resource "aws_cloudwatch_event_target" "merge_published" {
  rule     = aws_cloudwatch_event_rule.merge_published.name
  arn      = aws_sfn_state_machine.analytics.arn
  role_arn = var.role_arn

  input_transformer {
    input_paths    = { key = "$.detail.object.key" }
    input_template = "{\"s3_key\": <key>}"
  }
}

resource "aws_scheduler_schedule" "weekly" {
  count                        = var.weekly_schedule == "" ? 0 : 1
  name                         = "${var.name_prefix}-analytics-weekly"
  state                        = var.trigger_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.weekly_schedule
  schedule_expression_timezone = "Asia/Bangkok"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.analytics.arn
    role_arn = var.role_arn
    # mode=force: recompute every merged period, not only those whose merge marker is newer
    # (that case already runs from the merge_published rule)
    input = "{\"scheduled\":true,\"mode\":\"force\"}"

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.analytics.arn
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}
