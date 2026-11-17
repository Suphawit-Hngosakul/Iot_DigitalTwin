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
