# Root module (spec 00 §IaC). Learner Lab: us-east-1, every service uses LabRole.
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

# Learner Lab cannot create IAM roles -> use the existing LabRole.
data "aws_iam_role" "lab" {
  name = "LabRole"
}

module "storage" {
  source      = "./modules/storage"
  bucket_name = var.bucket_name
}

module "ingest" {
  source              = "./modules/ingest"
  name_prefix         = var.name_prefix
  role_arn            = data.aws_iam_role.lab.arn
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
  ssh_cidrs           = var.relay_ssh_cidrs
  hub_enabled         = var.hub_enabled
  hub_source_dir      = "${path.root}/../realtime-hub"
  device_token_hashes = var.device_token_hashes
}

# IoT hourly pipeline (spec 30, #11-#16).
module "pipeline" {
  source            = "./modules/pipeline"
  name_prefix       = var.name_prefix
  role_arn          = data.aws_iam_role.lab.arn
  bucket_name       = module.storage.bucket_name
  functions_dir     = "${path.root}/../pipeline/functions"
  common_dir        = "${path.root}/../pipeline/common"
  statemachine_file = "${path.root}/../pipeline/statemachine/pipeline.asl.json"
  alert_email       = var.alert_email
  schedule_enabled  = var.pipeline_schedule_enabled
  pandas_layer_arn  = var.pandas_layer_arn
}

# Glue Data Catalog + Athena (spec 40 §2, #17). Partition projection -> no crawler.
module "catalog" {
  source               = "./modules/catalog"
  bucket_name          = module.storage.bucket_name
  devices_csv          = "${path.root}/../pipeline/common/devices.csv"
  date_start           = var.catalog_date_start
  bytes_scanned_cutoff = var.athena_bytes_scanned_cutoff
}

# API for the web (spec 40 §3, #18). Reads Athena + publish markers, own HTTP API.
module "api" {
  source           = "./modules/api"
  name_prefix      = var.name_prefix
  role_arn         = data.aws_iam_role.lab.arn
  bucket_name      = module.storage.bucket_name
  source_dir       = "${path.root}/../api"
  common_dir       = "${path.root}/../pipeline/common"
  athena_database  = module.catalog.database_name
  athena_workgroup = module.catalog.workgroup_name
  athena_output    = module.catalog.results_location
  allowed_origins  = var.api_allowed_origins
}

# GEE connectivity test Lambda via Workload Identity Federation (spec 50, #25).
# Off by default: needs gee-layer.zip built by cloud/satellite/layer/build_layer.py.
module "satellite" {
  count        = var.satellite_enabled ? 1 : 0
  source       = "./modules/satellite"
  name_prefix  = var.name_prefix
  role_arn     = data.aws_iam_role.lab.arn
  handler_file = "${path.root}/../satellite/test_lambda/handler.py"
  layer_zip    = var.gee_layer_zip != "" ? var.gee_layer_zip : "${path.root}/../satellite/layer/build/gee-layer.zip"
  gee_project  = var.gee_project
  gee_assets   = var.gee_assets
  gee_wif      = var.gee_wif
}
