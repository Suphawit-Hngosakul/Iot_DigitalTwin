# curated-merge (spec 60 §2.1, issue #32, D23): 4 Lambda + Step Functions that MERGE
# satellite_grid + buildings_grid + readings_1min into Iceberg iot_walking.cell_period.
# Names: <prefix>-curated-* (spec 00). Shares common/ with the other pipelines.
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
variable "common_dir" { type = string }
variable "statemachine_file" { type = string }
variable "athena_database" { type = string }
variable "athena_workgroup" { type = string }

variable "alert_topic_arn" {
  description = "SNS topic for PipelineFailed (same topic as the other pipelines)"
  type        = string
}

variable "trigger_enabled" {
  description = "Start curated-merge when a satellite / buildings batch is published, and every day at daily_schedule_time"
  type        = bool
  default     = true
}

variable "daily_schedule" {
  description = "EventBridge Scheduler expression (Asia/Bangkok) for the daily run that picks up late IoT data; \"\" = no daily run"
  type        = string
  default     = "cron(0 10 * * ? *)"
}

locals {
  common_files = ["__init__.py", "schema.py", "curated.py", "satellite_schema.py", "grid.py", "devices.csv"]

  functions = {
    plan_merge     = { timeout = 120, memory = 256 }
    check_sources  = { timeout = 60, memory = 256 }
    merge_curated  = { timeout = 900, memory = 256 }
    verify_curated = { timeout = 300, memory = 256 }
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
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name_prefix}-curated-${replace(each.key, "_", "-")}"
  retention_in_days = 14
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-curated-${replace(each.key, "_", "-")}"
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.fn[each.key].output_path
  source_code_hash = data.archive_file.fn[each.key].output_base64sha256
  memory_size      = each.value.memory
  timeout          = each.value.timeout

  environment {
    variables = {
      DATA_BUCKET      = var.bucket_name
      ATHENA_DATABASE  = var.athena_database
      ATHENA_WORKGROUP = var.athena_workgroup
    }
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_sfn_state_machine" "merge" {
  name     = "${var.name_prefix}-curated-merge"
  role_arn = var.role_arn
  type     = "STANDARD"
  definition = templatefile(var.statemachine_file, {
    plan_merge_arn     = aws_lambda_function.fn["plan_merge"].arn
    check_sources_arn  = aws_lambda_function.fn["check_sources"].arn
    merge_curated_arn  = aws_lambda_function.fn["merge_curated"].arn
    verify_curated_arn = aws_lambda_function.fn["verify_curated"].arn
    alert_topic_arn    = var.alert_topic_arn
  })
}

# A published satellite / buildings batch writes curated/_success/<source>/<batch>.json
# (VerifyBatch). S3 -> EventBridge is on when satellite_pipeline_enabled = true (storage module).
resource "aws_cloudwatch_event_rule" "published" {
  name        = "${var.name_prefix}-curated-published"
  description = "satellite / buildings batch published -> curated-merge (spec 60 §2.1)"
  state       = var.trigger_enabled ? "ENABLED" : "DISABLED"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.bucket_name] }
      object = { key = [
        { wildcard = "curated/_success/satellite/*.json" },
        { wildcard = "curated/_success/buildings/*.json" },
      ] }
    }
  })
}

resource "aws_cloudwatch_event_target" "published" {
  rule     = aws_cloudwatch_event_rule.published.name
  arn      = aws_sfn_state_machine.merge.arn
  role_arn = var.role_arn # Learner Lab: LabRole starts the execution

  input_transformer {
    input_paths    = { key = "$.detail.object.key" }
    input_template = "{\"s3_key\": <key>}"
  }
}

# Daily run: IoT data that arrived late makes a period stale again (PlanMerge decides).
resource "aws_scheduler_schedule" "daily" {
  count                        = var.daily_schedule == "" ? 0 : 1
  name                         = "${var.name_prefix}-curated-merge-daily"
  state                        = var.trigger_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.daily_schedule
  schedule_expression_timezone = "Asia/Bangkok"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.merge.arn
    role_arn = var.role_arn
    input    = "{\"scheduled\":true}"

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.merge.arn
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}
