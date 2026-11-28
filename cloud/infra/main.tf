# Root module (spec 00 §IaC). Own AWS account: each service has its own IAM role (modules/iam).
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "iot-digitaltwin" }
  }
}


module "storage" {
  source      = "./modules/storage"
  bucket_name = var.bucket_name
  region      = var.region
  # S3 -> EventBridge for the satellite pipeline trigger (spec 51 §2)
  eventbridge_enabled = var.satellite_pipeline_enabled
  # web reads layer PNGs via presigned GET (#34) -> same origins as the API
  cors_allowed_origins = var.api_allowed_origins
}

module "ingest" {
  source              = "./modules/ingest"
  name_prefix         = var.name_prefix
  role_arn            = module.iam.lambda_role_arns["ingest"]
  bucket_name         = module.storage.bucket_name
  device_token_hashes = var.device_token_hashes
  source_dir          = "${path.root}/../ingest"
  common_dir          = "${path.root}/../pipeline/common"
}

# Plain HTTP relay for boards without RAM for TLS (decision 2026-11-17),
# plus the realtime hub on the same EC2 (ws:// :8081, D22, spec 22).
module "relay" {
  count               = var.relay_enabled ? 1 : 0
  source              = "./modules/relay"
  name_prefix         = var.name_prefix
  upstream_url        = module.ingest.ingest_url
  source_dir          = "${path.root}/../relay"
  hub_enabled         = var.hub_enabled
  hub_source_dir      = "${path.root}/../realtime-hub"
  device_token_hashes = var.device_token_hashes
}

# IoT hourly pipeline (spec 30, #11-#16).
module "pipeline" {
  source            = "./modules/pipeline"
  name_prefix       = var.name_prefix
  role_arn          = module.iam.lambda_role_arns["pipeline"]
  sfn_role_arn      = module.iam.sfn_role_arn
  trigger_role_arn  = module.iam.trigger_role_arn
  bucket_name       = module.storage.bucket_name
  functions_dir     = "${path.root}/../pipeline/functions"
  common_dir        = "${path.root}/../pipeline/common"
  statemachine_file = "${path.root}/../pipeline/statemachine/pipeline.asl.json"
  alert_email       = var.alert_email
  schedule_enabled  = var.pipeline_schedule_enabled
  pandas_layer_arn  = local.pandas_layer_arn
}

# Glue Data Catalog + Athena (spec 40 §2, #17). Partition projection -> no crawler.
module "catalog" {
  source               = "./modules/catalog"
  bucket_name          = module.storage.bucket_name
  devices_csv          = "${path.root}/../pipeline/common/devices.csv"
  date_start           = var.catalog_date_start
  bytes_scanned_cutoff = var.athena_bytes_scanned_cutoff
}

# IAM roles per service (plan R1): replaces the Learner Lab LabRole.
data "aws_caller_identity" "current" {}

module "iam" {
  source           = "./modules/iam"
  name_prefix      = var.name_prefix
  bucket_name      = module.storage.bucket_name
  athena_database  = module.catalog.database_name
  athena_workgroup = module.catalog.workgroup_name
}

# API for the web (spec 40 §3, #18). Reads Athena + publish markers, own HTTP API.
module "api" {
  source           = "./modules/api"
  name_prefix      = var.name_prefix
  role_arn         = module.iam.lambda_role_arns["api"]
  bucket_name      = module.storage.bucket_name
  source_dir       = "${path.root}/../api"
  common_dir       = "${path.root}/../pipeline/common"
  athena_database  = module.catalog.database_name
  athena_workgroup = module.catalog.workgroup_name
  athena_output    = module.catalog.results_location
  allowed_origins  = var.api_allowed_origins
}

# GEE Lambdas via Workload Identity Federation (spec 50): test (#25) + satellite_ingest (#27).
# Off by default. gee-layer.zip is built on apply by cloud/satellite/layer/build_layer.py
# (needs python + pip + internet on the machine running terraform), unless gee_layer_zip is set.
module "satellite" {
  count        = var.satellite_enabled ? 1 : 0
  source       = "./modules/satellite"
  name_prefix  = var.name_prefix
  role_arn     = module.iam.lambda_role_arns["satellite"]
  handler_file = "${path.root}/../satellite/test_lambda/handler.py"
  layer_zip    = var.gee_layer_zip != "" ? var.gee_layer_zip : "${path.root}/../satellite/layer/build/gee-layer.zip"
  # Build the zip on apply only when no prebuilt zip is given.
  layer_build_script = var.gee_layer_zip != "" ? "" : "${path.root}/../satellite/layer/build_layer.py"
  python_cmd         = var.python_cmd
  gee_project        = var.gee_project
  gee_assets         = var.gee_assets
  gee_wif            = var.gee_wif

  # satellite_ingest (#27): Landsat -> S3 raw/satellite/, same layer + WIF as the test Lambda
  ingest_source_dir      = "${path.root}/../satellite/ingest"
  data_bucket            = module.storage.bucket_name
  ingest_schedule        = var.satellite_ingest_schedule
  ingest_max_cloud_pct   = var.satellite_max_cloud_pct
  ingest_min_valid_ratio = var.satellite_min_valid_ratio
}

# Satellite pipeline (spec 51, #30-#31, D23): raw/satellite/<batch>/_COMPLETE ->
# Check -> Validate (contract) -> WriteTrusted -> RenderLayers -> Verify.
# Independent of satellite_enabled: it only reads what satellite_ingest wrote to S3.
module "satellite_pipeline" {
  count             = var.satellite_pipeline_enabled ? 1 : 0
  source            = "./modules/satellite_pipeline"
  name_prefix       = var.name_prefix
  role_arn          = module.iam.lambda_role_arns["satellite_pipeline"]
  sfn_role_arn      = module.iam.sfn_role_arn
  trigger_role_arn  = module.iam.trigger_role_arn
  bucket_name       = module.storage.bucket_name
  functions_dir     = "${path.root}/../satellite/pipeline/functions"
  common_dir        = "${path.root}/../pipeline/common"
  contracts_dir     = "${path.root}/../pipeline/contracts"
  statemachine_file = "${path.root}/../satellite/pipeline/statemachine/satellite-pipeline.asl.json"
  alert_topic_arn   = module.pipeline.alert_topic_arn
  pandas_layer_arn  = local.pandas_layer_arn
  trigger_enabled   = var.satellite_pipeline_trigger_enabled
}

# curated-merge (spec 60 §2.1, #32, D23): satellite_grid + buildings_grid + readings_1min ->
# Iceberg iot_walking.cell_period (Athena MERGE). Triggered by published satellite/buildings batches
# (needs satellite_pipeline_enabled for the S3 -> EventBridge notification) + a daily run.
module "curated_merge" {
  count             = var.curated_merge_enabled ? 1 : 0
  source            = "./modules/curated_merge"
  name_prefix       = var.name_prefix
  bucket_name       = module.storage.bucket_name
  role_arn          = module.iam.lambda_role_arns["curated_merge"]
  sfn_role_arn      = module.iam.sfn_role_arn
  trigger_role_arn  = module.iam.trigger_role_arn
  functions_dir     = "${path.root}/../analytics/curated_merge/functions"
  common_dir        = "${path.root}/../pipeline/common"
  statemachine_file = "${path.root}/../analytics/curated_merge/statemachine/curated-merge.asl.json"
  alert_topic_arn   = module.pipeline.alert_topic_arn
  athena_database   = module.catalog.database_name
  athena_workgroup  = module.catalog.workgroup_name
  trigger_enabled   = var.curated_merge_trigger_enabled
  daily_schedule    = var.curated_merge_daily_schedule
}

# Heat analytics S1-S8 (Spec 60 §3-4, #33): reads iot_walking.cell_period ->
# writes JSON + Parquet reports to curated/analytics/.
module "analytics" {
  count             = var.analytics_enabled ? 1 : 0
  source            = "./modules/analytics"
  name_prefix       = var.name_prefix
  role_arn          = module.iam.lambda_role_arns["analytics"]
  sfn_role_arn      = module.iam.sfn_role_arn
  trigger_role_arn  = module.iam.trigger_role_arn
  bucket_name       = module.storage.bucket_name
  functions_dir     = "${path.root}/../analytics/functions"
  stats_dir         = "${path.root}/../analytics/stats"
  common_dir        = "${path.root}/../pipeline/common"
  statemachine_file = "${path.root}/../analytics/statemachine/analytics.asl.json"
  alert_topic_arn   = module.pipeline.alert_topic_arn
  athena_database   = module.catalog.database_name
  athena_workgroup  = module.catalog.workgroup_name
  pandas_layer_arn  = local.pandas_layer_arn
  trigger_enabled   = var.analytics_trigger_enabled
  weekly_schedule   = var.analytics_weekly_schedule
}

# Web: private S3 + CloudFront (HTTPS, no domain) + config.json (plan R4, #35).
# /live* on CloudFront goes to the hub, so the page uses wss:// (no mixed content).
# Build first: cd web && npm run build
module "web" {
  source            = "./modules/web"
  name_prefix       = var.name_prefix
  dist_dir          = "${path.root}/../../web/dist"
  api_url           = module.api.api_url
  hub_origin_domain = var.relay_enabled && var.hub_enabled ? module.relay[0].public_dns : ""
  hub_port          = var.relay_enabled ? module.relay[0].hub_port : 8081
}

# Public AWS SDK for pandas layer (pyarrow) of the deploy region (plan R6).
locals {
  pandas_layer_arn = var.pandas_layer_arn != "" ? var.pandas_layer_arn : (
    "arn:aws:lambda:${var.region}:336392948345:layer:AWSSDKPandas-Python312:${var.pandas_layer_version}"
  )
}
