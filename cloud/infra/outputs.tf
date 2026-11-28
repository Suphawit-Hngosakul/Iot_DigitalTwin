output "bucket" {
  value = module.storage.bucket_name
}

output "ingest_url" {
  description = "Put in .env as INGEST_URL and in the firmware config"
  value       = module.ingest.ingest_url
}

output "relay_url" {
  description = "Put in the firmware as INGEST_URL (plain HTTP, see cloud/relay)"
  value       = var.relay_enabled ? module.relay[0].relay_url : null
}

output "hub_device_url" {
  description = "Put in the firmware as wssUrl (plain ws://, D22)"
  value       = var.relay_enabled ? module.relay[0].hub_device_url : null
}

output "hub_live_url" {
  description = "For live.html / the web: <this>?device=all"
  value       = var.relay_enabled ? module.relay[0].hub_live_url : null
}

output "pipeline_state_machine_arn" {
  description = "aws stepfunctions start-execution --state-machine-arn <this> --input '{\"batch_ids\":[...]}'"
  value       = module.pipeline.state_machine_arn
}

output "athena_database" {
  description = "ATHENA_DB for the API (#18)"
  value       = module.catalog.database_name
}

output "athena_workgroup" {
  description = "ATHENA_WORKGROUP for the API (#18); pick it in the Athena console before querying"
  value       = module.catalog.workgroup_name
}

output "athena_output" {
  description = "ATHENA_OUTPUT for the API (#18)"
  value       = module.catalog.results_location
}

output "catalog_device_ids" {
  description = "device_id values in the partition projection (from devices.csv)"
  value       = module.catalog.device_ids
}

output "api_url" {
  description = "Base URL of the web API (#18), e.g. <this>/devices"
  value       = module.api.api_url
}

output "satellite_function_name" {
  description = "GEE test Lambda (#25), null when satellite_enabled = false"
  value       = one(module.satellite[*].function_name)
}

output "gee_layer_arn" {
  description = "GEE Lambda layer ARN, reuse for satellite_ingest (#27); null when satellite_enabled = false"
  value       = one(module.satellite[*].layer_arn)
}

output "satellite_ingest_function_name" {
  description = "satellite_ingest Lambda (#27), null when satellite_enabled = false"
  value       = one(module.satellite[*].ingest_function_name)
}

output "satellite_pipeline_state_machine_arn" {
  description = "Satellite pipeline (#30-#31); start by hand with {\"batch_id\": \"scene_YYYY-MM-DD\", \"mode\": \"normal\"}. null when satellite_pipeline_enabled = false"
  value       = one(module.satellite_pipeline[*].state_machine_arn)
}

output "curated_merge_state_machine_arn" {
  description = "curated-merge (#32); start by hand with {\"periods\": [\"scene_2024-03-17\"], \"mode\": \"force\"} or {} to merge whatever is stale. null when curated_merge_enabled = false"
  value       = one(module.curated_merge[*].state_machine_arn)
}

output "analytics_state_machine_arn" {
  description = "Heat analytics (#33); start by hand with {\"periods\": [\"season_2025_03-05\"], \"mode\": \"force\"} or {}. null when analytics_enabled = false"
  value       = one(module.analytics[*].state_machine_arn)
}

output "satellite_wif_role" {
  description = "Send to the GEE project owner: grant roles/iam.workloadIdentityUser to principalSet .../attribute.aws_role/<this>"
  value       = "arn:aws:sts::${data.aws_caller_identity.current.account_id}:assumed-role/${module.iam.satellite_role_name}"
}
