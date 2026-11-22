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

variable "hub_enabled" {
  description = "Run the realtime hub (ws:// :8081, spec 22) on the relay EC2. Needs relay_enabled."
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

variable "api_allowed_origins" {
  description = "CORS origins allowed to call the API (#18). [\"*\"] until the web (#35) has a domain, then e.g. [\"http://<bucket>.s3-website-us-east-1.amazonaws.com\"]."
  type        = list(string)
  default     = ["*"]
}

variable "satellite_enabled" {
  description = "Deploy the GEE layer + test Lambda (#25) + satellite_ingest with its daily schedule (#27). The layer zip is built on apply (see python_cmd / gee_layer_zip)."
  type        = bool
  default     = false
}

variable "gee_layer_zip" {
  description = "Path to a prebuilt gee-layer.zip. Empty = Terraform runs cloud/satellite/layer/build_layer.py on apply."
  type        = string
  default     = ""
}

variable "python_cmd" {
  description = "Python with pip used to build gee-layer.zip on apply (python on Windows, python3 on most Linux/macOS)"
  type        = string
  default     = "python"
}

variable "gee_project" {
  description = "Google Cloud project that owns the GEE assets (spec 50 §5, option A = Get's project)"
  type        = string
  default     = "turnkey-tribute-511903-t6"
}

variable "gee_assets" {
  description = "Asset the test Lambda reads to prove access"
  type        = string
  default     = "projects/turnkey-tribute-511903-t6/assets/laksi_boundary"
}

variable "gee_wif" {
  description = "Workload Identity Federation (AWS -> Google) used instead of a service-account key. Not secret."
  type = object({
    project_number  = string # numeric id of the project holding the identity pool
    pool_id         = string
    provider_id     = string
    service_account = string # email the Lambda impersonates
  })
  default = {
    project_number  = "959910184696"
    pool_id         = "6609650251"
    provider_id     = "aws-pool-gee-iot"
    service_account = "service-gee-iot@turnkey-tribute-511903-t6.iam.gserviceaccount.com"
  }
}

variable "satellite_ingest_schedule" {
  description = "When satellite_ingest (#27) runs. Default daily 02:00 UTC = 09:00 Thai (spec 50 §3). \"\" = manual runs only."
  type        = string
  default     = "cron(0 2 * * ? *)"
}

variable "satellite_max_cloud_pct" {
  description = "satellite_ingest skips a scene date with more cloud than this inside the AOI (spec 50 §5 MAX_CLOUD_PCT)"
  type        = number
  default     = 40
}

variable "satellite_min_valid_ratio" {
  description = "satellite_ingest also skips a scene date when fewer than this share of AOI pixels are clear (0-1)"
  type        = number
  default     = 0.15
}

variable "satellite_pipeline_enabled" {
  description = "Deploy the satellite pipeline (#30-#31, spec 51) and turn on S3 -> EventBridge notifications for the bucket"
  type        = bool
  default     = false
}

variable "satellite_pipeline_trigger_enabled" {
  description = "Start the satellite pipeline automatically for every raw/satellite/*/_COMPLETE. false = manual runs only."
  type        = bool
  default     = true
}

variable "curated_merge_enabled" {
  description = "Deploy curated-merge (#32, spec 60 §2.1): Athena MERGE into Iceberg cell_period"
  type        = bool
  default     = false
}

variable "curated_merge_trigger_enabled" {
  description = "Start curated-merge automatically (published satellite/buildings batch + daily run). false = manual runs only."
  type        = bool
  default     = true
}

variable "curated_merge_daily_schedule" {
  description = "Scheduler expression (Asia/Bangkok) of the daily run that picks up late IoT data; \"\" = none"
  type        = string
  default     = "cron(0 10 * * ? *)"
}

variable "analytics_enabled" {
  description = "Deploy heat analytics S1-S8 (#33, Spec 60 §3-4)"
  type        = bool
  default     = false
}

variable "analytics_trigger_enabled" {
  description = "Run analytics automatically when curated-merge publishes a period + weekly schedule"
  type        = bool
  default     = true
}

variable "analytics_weekly_schedule" {
  description = "Weekly Scheduler expression (Asia/Bangkok) for analytics refresh; \"\" = none"
  type        = string
  default     = "cron(0 11 ? * SUN *)"
}
