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

module "satellite" {
  source      = "./modules/satellite"
  name_prefix = var.name_prefix
  role_arn    = data.aws_iam_role.lab.arn
  source_dir  = "${path.root}/../satellite/test_lambda"
  layer_zip   = "${path.root}/../satellite/gee-layer.zip"
  gee_project = var.gee_project
  gee_assets  = var.gee_assets
}
