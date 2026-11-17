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
