# Satellite pipeline (spec 51, issues #30-#31, D23): 6 Lambda + Step Functions,
# started by EventBridge when satellite_ingest writes raw/satellite/<batch>/_COMPLETE.
# Names: <prefix>-sat-* (spec 00). Shares common/ with the IoT pipeline.
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
variable "contracts_dir" { type = string }
variable "statemachine_file" { type = string }

variable "alert_topic_arn" {
  description = "SNS topic for PipelineFailed (reuse the IoT pipeline topic -> one email subscription)"
  type        = string
}

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas layer (pyarrow) for write_trusted / verify_batch"
  type        = string
}

variable "trigger_enabled" {
  description = "Start an execution for every raw/satellite/*/_COMPLETE. false = manual runs only."
  type        = bool
  default     = true
}

locals {
  common_files = [
    "__init__.py", "schema.py", "batch_manifest.py", "grid.py", "devices.csv",
    "satellite_schema.py", "satellite_grid.py", "satellite_batch.py", "contract.py",
  ]
  # PyYAML is not in the Lambda runtime: ship contracts as JSON (contract.load reads .json first).
  contracts = ["satellite_scene", "satellite_season", "buildings"]

  functions = {
    check_batch      = { timeout = 60, memory = 256, layers = [] }
    validate_batch   = { timeout = 300, memory = 1024, layers = [] }
    quarantine_batch = { timeout = 120, memory = 256, layers = [] }
    write_trusted    = { timeout = 300, memory = 1024, layers = [var.pandas_layer_arn] }
    render_layers    = { timeout = 300, memory = 1024, layers = [] }
    verify_batch     = { timeout = 300, memory = 1024, layers = [var.pandas_layer_arn] }
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
    for_each = local.contracts
    content {
      content  = jsonencode(yamldecode(file("${var.contracts_dir}/${source.value}.yaml")))
      filename = "contracts/${source.value}.json"
    }
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name_prefix}-sat-${replace(each.key, "_", "-")}"
  retention_in_days = 14
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${var.name_prefix}-sat-${replace(each.key, "_", "-")}"
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.fn[each.key].output_path
  source_code_hash = data.archive_file.fn[each.key].output_base64sha256
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  layers           = each.value.layers

  environment {
    variables = { DATA_BUCKET = var.bucket_name }
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name_prefix}-sat-pipeline"
  role_arn = var.role_arn
  type     = "STANDARD"
  definition = templatefile(var.statemachine_file, {
    check_batch_arn      = aws_lambda_function.fn["check_batch"].arn
    validate_batch_arn   = aws_lambda_function.fn["validate_batch"].arn
    quarantine_batch_arn = aws_lambda_function.fn["quarantine_batch"].arn
    write_trusted_arn    = aws_lambda_function.fn["write_trusted"].arn
    render_layers_arn    = aws_lambda_function.fn["render_layers"].arn
    verify_batch_arn     = aws_lambda_function.fn["verify_batch"].arn
    alert_topic_arn      = var.alert_topic_arn
  })
}

# S3 "Object Created" events reach EventBridge because the storage module turns on
# bucket notifications to EventBridge (eventbridge_enabled). 1 _COMPLETE = 1 execution.
resource "aws_cloudwatch_event_rule" "complete" {
  name        = "${var.name_prefix}-sat-complete"
  description = "satellite_ingest finished a batch folder -> satellite pipeline (spec 51 §2)"
  state       = var.trigger_enabled ? "ENABLED" : "DISABLED"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.bucket_name] }
      object = { key = [{ wildcard = "raw/satellite/*/_COMPLETE" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "pipeline" {
  rule     = aws_cloudwatch_event_rule.complete.name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = var.role_arn # Learner Lab: LabRole starts the execution

  input_transformer {
    input_paths    = { key = "$.detail.object.key" }
    input_template = "{\"s3_key\": <key>, \"mode\": \"normal\"}"
  }
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "function_names" {
  value = { for k, f in aws_lambda_function.fn : k => f.function_name }
}
