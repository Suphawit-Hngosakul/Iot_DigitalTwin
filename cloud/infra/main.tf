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

# Plain HTTP relay for boards without RAM for TLS (decision 2026-11-17).
module "relay" {
  count        = var.relay_enabled ? 1 : 0
  source       = "./modules/relay"
  name_prefix  = var.name_prefix
  upstream_url = module.ingest.ingest_url
  source_dir   = "${path.root}/../relay"
  ssh_cidrs    = var.relay_ssh_cidrs
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
