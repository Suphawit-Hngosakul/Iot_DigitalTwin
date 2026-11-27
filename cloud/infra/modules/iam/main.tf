# IAM roles per service (plan R1). Replaces the Learner Lab LabRole:
# each Lambda group gets only the S3 / Athena / Glue access its code uses,
# Step Functions and the triggers (EventBridge rule + Scheduler) get their own roles.
# ARNs use the name prefix, not module outputs, so there is no dependency cycle.
variable "name_prefix" { type = string }
variable "bucket_name" { type = string }
variable "athena_database" { type = string }
variable "athena_workgroup" { type = string }

data "aws_caller_identity" "this" {}
data "aws_region" "this" {}
data "aws_partition" "this" {}

locals {
  p      = data.aws_partition.this.partition
  region = data.aws_region.this.name
  acct   = data.aws_caller_identity.this.account_id

  bucket_arn  = "arn:${local.p}:s3:::${var.bucket_name}"
  lambda_arns = "arn:${local.p}:lambda:${local.region}:${local.acct}:function:${var.name_prefix}-*"
  sfn_arns    = "arn:${local.p}:states:${local.region}:${local.acct}:stateMachine:${var.name_prefix}-*"
  sns_arns    = "arn:${local.p}:sns:${local.region}:${local.acct}:${var.name_prefix}-*"

  # s3: "ingest" = put raw/ only, "read" = get/list + put athena-results/, "rw" = get/put/delete/list
  # athena: "none" | "read" (SELECT) | "write" (INSERT/MERGE/CTAS on Iceberg)
  lambda_roles = {
    ingest             = { s3 = "ingest", athena = "none" }
    api                = { s3 = "read", athena = "read" }
    pipeline           = { s3 = "rw", athena = "none" }
    satellite          = { s3 = "rw", athena = "none" }
    satellite_pipeline = { s3 = "rw", athena = "none" }
    curated_merge      = { s3 = "rw", athena = "write" }
    analytics          = { s3 = "rw", athena = "write" }
  }

  s3_statements = {
    ingest = [
      { actions = ["s3:PutObject"], resources = ["${local.bucket_arn}/raw/*"] },
    ]
    read = [
      { actions = ["s3:ListBucket", "s3:GetBucketLocation"], resources = [local.bucket_arn] },
      { actions = ["s3:GetObject"], resources = ["${local.bucket_arn}/*"] },
      { actions = ["s3:PutObject", "s3:AbortMultipartUpload"], resources = ["${local.bucket_arn}/athena-results/*"] },
    ]
    rw = [
      { actions = ["s3:ListBucket", "s3:GetBucketLocation"], resources = [local.bucket_arn] },
      { actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], resources = ["${local.bucket_arn}/*"] },
    ]
  }

  athena_resources = [
    "arn:${local.p}:athena:${local.region}:${local.acct}:workgroup/${var.athena_workgroup}",
  ]
  glue_resources = [
    "arn:${local.p}:glue:${local.region}:${local.acct}:catalog",
    "arn:${local.p}:glue:${local.region}:${local.acct}:database/${var.athena_database}",
    "arn:${local.p}:glue:${local.region}:${local.acct}:table/${var.athena_database}/*",
  ]
  athena_read_actions = [
    "athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults",
    "athena:StopQueryExecution", "athena:GetWorkGroup",
  ]
  glue_read_actions = [
    "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
    "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition",
  ]
  glue_write_actions = [
    "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable",
    "glue:CreatePartition", "glue:BatchCreatePartition", "glue:UpdatePartition",
    "glue:DeletePartition", "glue:BatchDeletePartition",
  ]
}

# ---- Lambda roles -------------------------------------------------------------

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each           = local.lambda_roles
  name               = "${var.name_prefix}-${replace(each.key, "_", "-")}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  for_each   = local.lambda_roles
  role       = aws_iam_role.lambda[each.key].name
  policy_arn = "arn:${local.p}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  for_each = local.lambda_roles

  dynamic "statement" {
    for_each = local.s3_statements[each.value.s3]
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }

  dynamic "statement" {
    for_each = each.value.athena == "none" ? [] : [1]
    content {
      actions   = local.athena_read_actions
      resources = local.athena_resources
    }
  }

  dynamic "statement" {
    for_each = each.value.athena == "none" ? [] : [1]
    content {
      actions = concat(
        local.glue_read_actions,
        each.value.athena == "write" ? local.glue_write_actions : [],
      )
      resources = local.glue_resources
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  for_each = local.lambda_roles
  name     = "access"
  role     = aws_iam_role.lambda[each.key].id
  policy   = data.aws_iam_policy_document.lambda[each.key].json
}

# ---- Step Functions: invoke our Lambdas + publish PipelineFailed ----------------

data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [local.lambda_arns, "${local.lambda_arns}:*"]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [local.sns_arns]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "access"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

# ---- Triggers: EventBridge rules (S3 _COMPLETE / _success) + Scheduler ---------

data "aws_iam_policy_document" "trigger_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.acct]
    }
  }
}

resource "aws_iam_role" "trigger" {
  name               = "${var.name_prefix}-trigger"
  assume_role_policy = data.aws_iam_policy_document.trigger_trust.json
}

data "aws_iam_policy_document" "trigger" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [local.sfn_arns]
  }
}

resource "aws_iam_role_policy" "trigger" {
  name   = "access"
  role   = aws_iam_role.trigger.id
  policy = data.aws_iam_policy_document.trigger.json
}

# ---- Outputs -------------------------------------------------------------------

output "lambda_role_arns" {
  description = "Lambda execution role ARN per service (keys = local.lambda_roles)"
  value       = { for k, r in aws_iam_role.lambda : k => r.arn }
}

output "satellite_role_name" {
  description = "Give this to the GEE project owner: WIF principal attribute.aws_role = arn:aws:sts::<acct>:assumed-role/<name>"
  value       = aws_iam_role.lambda["satellite"].name
}

output "sfn_role_arn" {
  value = aws_iam_role.sfn.arn
}

output "trigger_role_arn" {
  value = aws_iam_role.trigger.arn
}
