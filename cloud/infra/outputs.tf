output "bucket" {
  value = module.storage.bucket_name
}

output "ingest_url" {
  description = "Put in .env as INGEST_URL and in the firmware config"
  value       = module.ingest.ingest_url
}

output "satellite_function_name" {
  description = "Name of the GEE test Lambda function"
  value       = module.satellite.function_name
}

output "satellite_function_arn" {
  description = "ARN of the GEE test Lambda function"
  value       = module.satellite.function_arn
}

output "gee_layer_arn" {
  description = "ARN of the GEE Lambda Layer"
  value       = module.satellite.layer_arn
}
