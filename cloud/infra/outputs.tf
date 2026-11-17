output "bucket" {
  value = module.storage.bucket_name
}

output "ingest_url" {
  description = "Put in .env as INGEST_URL and in the firmware config"
  value       = module.ingest.ingest_url
}
