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

variable "alert_email" {
  description = "Email for pipeline failure alerts (SNS). Confirm the subscription mail once. Empty = no email."
  type        = string
  default     = ""
}

variable "pipeline_schedule_enabled" {
  description = "Run the IoT pipeline every hour at minute 5. false = manual runs only."
  type        = bool
  default     = true
}

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas Lambda layer for Python 3.12 in us-east-1 (see aws-sdk-pandas install docs, 'AWS Lambda Layer')."
  type        = string
}

variable "catalog_date_start" {
  description = "First Thai-local date the Glue tables can see (partition projection, yyyy-MM-dd). Keep it close to the first real data: a query without a date filter lists every projected partition in S3 (devices x days x 24 for raw_csv), so an early start makes it very slow."
  type        = string
  default     = "2026-11-01"
}

variable "athena_bytes_scanned_cutoff" {
  description = "Athena cancels a query that scans more than this (bytes, min 10 MB). Protects the lab budget from queries without a partition filter."
  type        = number
  default     = 1073741824 # 1 GiB
}
