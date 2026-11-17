variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for Lambda / API names"
  type        = string
  default     = "iot-dt"
}

variable "bucket_name" {
  description = "Data lake bucket (globally unique)"
  type        = string
}

variable "device_token_hashes" {
  description = "{ sha256(token) = device_id } from cloud/ingest/make_token.py. Hashes only, never raw tokens."
  type        = map(string)
  sensitive   = true
}

variable "gee_project" {
  description = "Google Earth Engine / Google Cloud Project ID"
  type        = string
  default     = "turnkey-tribute-511903-t6"
}

variable "gee_assets" {
  description = "Asset path to verify in GEE"
  type        = string
  default     = "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
}
