# IoT hourly pipeline (spec 30, issues #11-#16): 6 Lambda + Step Functions +
# EventBridge Scheduler + SNS alerts. Names: <prefix>-iot-* (spec 00).
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

variable "alert_email" {
  description = "SNS email for PipelineFailed (confirm the subscription mail once). Empty = no subscription."
  type        = string
  default     = ""
}

variable "schedule_enabled" {
  description = "Run every hour at minute 5 (Asia/Bangkok). false = manual runs only."
  type        = bool
  default     = true
}

variable "lookback_hours" {
  type    = number
  default = 24
}

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas layer (pyarrow) for transform/verify, e.g. arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:<version>"
  type        = string
}

locals {
  # Shipped as package `common/` next to handler.py (grid.py includes pure-Python UTM 47N fallback when pyproj is absent).
  common_files = ["__init__.py", "schema.py", "batch_manifest.py", "batch_eval.py", "devices.csv", "grid.py"]

  functions = {
    plan_batches     = { timeout = 60, memory = 256, layers = [], env = { LOOKBACK_HOURS = tostring(var.lookback_hours) } }
    check_batch      = { timeout = 60, memory = 256, layers = [], env = {} }
    validate_batch   = { timeout = 120, memory = 512, layers = [], env = {} }
    quarantine_batch = { timeout = 120, memory = 256, layers = [], env = {} }
    transform_batch  = { timeout = 300, memory = 1024, layers = [var.pandas_layer_arn], env = {} }
    verify_batch     = { timeout = 120, memory = 512, layers = [var.pandas_layer_arn], env = {} }
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
  name              = "/aws/lambda/${var.name_prefix}-iot-${replace(each.key, "_", "-")}"
  retention_in_days = 14
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-iot-${replace(each.key, "_", "-")}"
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.fn[each.key].output_path
  source_code_hash = data.archive_file.fn[each.key].output_base64sha256
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  layers           = each.value.layers

  environment {
    variables = merge({ DATA_BUCKET = var.bucket_name }, each.value.env)
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-iot-pipeline-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name_prefix}-iot-pipeline"
  role_arn = var.role_arn
  type     = "STANDARD"
  definition = templatefile(var.statemachine_file, {
    plan_batches_arn     = aws_lambda_function.fn["plan_batches"].arn
    check_batch_arn      = aws_lambda_function.fn["check_batch"].arn
    validate_batch_arn   = aws_lambda_function.fn["validate_batch"].arn
    quarantine_batch_arn = aws_lambda_function.fn["quarantine_batch"].arn
    transform_batch_arn  = aws_lambda_function.fn["transform_batch"].arn
    verify_batch_arn     = aws_lambda_function.fn["verify_batch"].arn
    alert_topic_arn      = aws_sns_topic.alerts.arn
  })
}

# Learner Lab: the Scheduler assumes LabRole to start the state machine.
resource "aws_scheduler_schedule" "hourly" {
  name                         = "${var.name_prefix}-iot-pipeline-hourly"
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = "cron(5 * * * ? *)"
  schedule_expression_timezone = "Asia/Bangkok"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = var.role_arn
    # <aws.scheduler.scheduled-time> -> PlanBatches derives hour H from it.
    # Literal string, not jsonencode(): jsonencode escapes < > as < >
    # and the Scheduler only substitutes the unescaped placeholder.
    input = "{\"mode\":\"normal\",\"scheduled_time\":\"<aws.scheduler.scheduled-time>\"}"

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "alert_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}
