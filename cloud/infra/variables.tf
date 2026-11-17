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

variable "relay_enabled" {
  description = "EC2 HTTP -> HTTPS relay for the ESP32 (no RAM for TLS). Costs an always-on t3.micro while the lab runs."
  type        = bool
  default     = true
}

variable "relay_ssh_cidrs" {
  description = "Open port 22 on the relay for logs, e.g. [\"18.206.107.24/29\"] for EC2 Instance Connect in us-east-1. Empty = closed."
  type        = list(string)
  default     = []
}
