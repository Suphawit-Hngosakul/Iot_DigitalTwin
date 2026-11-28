# Glue Data Catalog + Athena (spec 40 §2, issue #17).
#
# Partition projection: Athena computes partitions from the table properties,
# so no crawler / MSCK REPAIR is needed when new files land in S3.
#   device_id = enum from cloud/pipeline/common/devices.csv (re-apply after adding a device)
#   date      = Thai-local date range (string 'yyyy-MM-dd')
#   hour      = 00-23 (raw only)
#
# Rows without a trusted time land in raw/csv/.../date=unknown/hour=unknown/
# (spec 30 §2). A date/integer projection cannot see 'unknown', so they get
# their own table raw_csv_unknown (decided in #17). Processed tables only hold
# rows with a valid time.
#
# satellite_grid (spec 51 §4, #31) uses the same approach with its own keys:
#   period_type = enum scene,season  (trusted/satellite_grid/period_type=<t>/)
#   year        = integer satellite_year_range (Landsat 8 starts 2013)
# buildings_grid (spec 51 §4, #32): trusted/buildings_grid/year=<YYYY>/, same year range.

variable "bucket_name" {
  type = string
}

variable "database_name" {
  type    = string
  default = "iot_walking"
}

variable "workgroup_name" {
  type    = string
  default = "iot_walking"
}

variable "devices_csv" {
  description = "Path to devices.csv; its device_id column becomes the device_id projection enum"
  type        = string
}

variable "date_start" {
  description = "First Thai-local date in the date projection (yyyy-MM-dd)"
  type        = string
}

variable "bytes_scanned_cutoff" {
  description = "Athena stops a query that scans more than this many bytes (protects the budget)"
  type        = number
}

variable "satellite_year_range" {
  description = "First,last year in the satellite_grid year projection (integer range, no NOW)"
  type        = string
  default     = "2013,2035"
}

locals {
  bucket_uri = "s3://${var.bucket_name}"

  device_ids = distinct([for d in csvdecode(file(var.devices_csv)) : trimspace(d.device_id)])

  projection_device = {
    "projection.device_id.type"   = "enum"
    "projection.device_id.values" = join(",", local.device_ids)
  }
  # NOW+1DAYS: Athena's NOW is UTC, Thai-local date is up to 7 h ahead.
  projection_date = {
    "projection.date.type"          = "date"
    "projection.date.format"        = "yyyy-MM-dd"
    "projection.date.range"         = "${var.date_start},NOW+1DAYS"
    "projection.date.interval"      = "1"
    "projection.date.interval.unit" = "DAYS"
  }

  # Raw CSV columns in file order (spec 01 §1). Names are lower-case because
  # Glue/Athena lower-case everything, and Device_ID is renamed so it does not
  # clash with the device_id partition key. Unparsable numbers read as NULL
  # (LazySimpleSerDe), so a bad row never breaks a query.
  raw_columns = [
    ["csv_device_id", "string"],
    ["device_type", "string"],
    ["session", "bigint"],
    ["record_id", "bigint"],
    ["datetime", "string"], # Thai time, no offset; seconds-since-boot without RTC
    ["dba", "double"],
    ["db_spl", "double"],
    ["lux", "double"],
    ["temp_c", "double"],
    ["humidity_pct", "double"],
    ["latitude", "double"],
    ["longitude", "double"],
    ["altitude_m", "double"],
    ["speed_kmh", "double"],
    ["satellites", "int"],
    ["hdop", "double"],
    ["gps_valid", "int"],
    ["lux_valid", "int"],
    ["dht_valid", "int"],
    ["time_valid", "int"],
    ["spl_valid", "int"],
    ["flags", "string"],
  ]

  # S3 prefix per IoT table; must match schema.TABLE_ZONE (D23 step 4).
  iot_prefix = {
    readings_1s   = "trusted/iot_readings_1s"
    readings_1min = "curated/readings_1min"
    quality       = "trusted/iot_quality"
  }

  # Must match schema.TABLES in cloud/pipeline/common/schema.py.
  iot_tables = {
    readings_1s = [
      ["device_type", "string"],
      ["session", "bigint"],
      ["record_id", "bigint"],
      ["ts_local", "timestamp"],
      ["ts_utc", "timestamp"],
      ["db_spl", "double"],
      ["dba", "double"],
      ["lux", "double"],
      ["temp_c", "double"],
      ["humidity_pct", "double"],
      ["lat", "double"],
      ["lon", "double"],
      ["alt_m", "double"],
      ["speed_kmh", "double"],
      ["satellites", "int"],
      ["hdop", "double"],
    ]
    readings_1min = [
      ["minute_local", "timestamp"],
      ["minute_utc", "timestamp"],
      ["n_rows", "int"],
      ["db_spl_mean", "double"],
      ["db_spl_max", "double"],
      ["db_spl_min", "double"],
      ["dba_mean", "double"],
      ["temp_c_mean", "double"],
      ["humidity_pct_mean", "double"],
      ["lux_mean", "double"],
      ["lat", "double"],
      ["lon", "double"],
      ["cell_id", "string"], # filled by #29 (grid.py); null when no fix or off-grid
    ]
    quality = [
      ["batch_id", "string"],
      ["session", "bigint"],
      ["expected_rows", "bigint"],
      ["received_rows", "bigint"],
      ["missing_rows", "bigint"],
      ["duplicate_rows", "bigint"],
      ["invalid_rows", "bigint"],
      ["loss_rate", "double"],
      ["flag_counts", "string"], # JSON
    ]
  }

  csv_serde = {
    serialization_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
    parameters = {
      "field.delim"          = ","
      "serialization.format" = ","
    }
  }
}

resource "aws_glue_catalog_database" "this" {
  name        = var.database_name
  description = "IoT walking data lake (spec 40)"
}

# --- raw CSV with a trusted time -------------------------------------------
resource "aws_glue_catalog_table" "raw_csv" {
  database_name = aws_glue_catalog_database.this.name
  name          = "raw_csv"
  description   = "Device CSV as uploaded, rows with a trusted time (raw/csv/)"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_device, local.projection_date, {
    "classification"            = "csv"
    "skip.header.line.count"    = "1"
    "projection.enabled"        = "true"
    "projection.hour.type"      = "integer"
    "projection.hour.range"     = "0,23"
    "projection.hour.digits"    = "2"
    "storage.location.template" = "${local.bucket_uri}/raw/csv/device_id=$${device_id}/date=$${date}/hour=$${hour}/"
  })

  partition_keys {
    name = "device_id"
    type = "string"
  }
  partition_keys {
    name = "date"
    type = "string"
  }
  partition_keys {
    name = "hour"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.bucket_uri}/raw/csv/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = local.csv_serde.serialization_library
      parameters            = local.csv_serde.parameters
    }

    dynamic "columns" {
      for_each = local.raw_columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# --- raw CSV without a trusted time (date=unknown/hour=unknown) ------------
# For counting / debugging RTC problems. Never enters the pipeline.
resource "aws_glue_catalog_table" "raw_csv_unknown" {
  database_name = aws_glue_catalog_database.this.name
  name          = "raw_csv_unknown"
  description   = "Device CSV rows without a trusted time (raw/csv/.../date=unknown/hour=unknown/)"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_device, {
    "classification"            = "csv"
    "skip.header.line.count"    = "1"
    "projection.enabled"        = "true"
    "storage.location.template" = "${local.bucket_uri}/raw/csv/device_id=$${device_id}/date=unknown/hour=unknown/"
  })

  partition_keys {
    name = "device_id"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.bucket_uri}/raw/csv/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = local.csv_serde.serialization_library
      parameters            = local.csv_serde.parameters
    }

    dynamic "columns" {
      for_each = local.raw_columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# --- IoT Parquet: trusted/ + curated/ (spec 01 §4, D23) ---------------------
# Renamed from aws_glue_catalog_table.processed (D23 step 4): without this block
# Terraform would delete the 3 tables and create them again in parallel, and the
# create fails with AlreadyExistsException. With it the tables are updated in place.
moved {
  from = aws_glue_catalog_table.processed
  to   = aws_glue_catalog_table.iot
}

resource "aws_glue_catalog_table" "iot" {
  for_each = local.iot_tables

  database_name = aws_glue_catalog_database.this.name
  name          = each.key
  description   = "Pipeline output ${local.iot_prefix[each.key]}/ (spec 01 §4, D23)"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_device, local.projection_date, {
    "classification"            = "parquet"
    "parquet.compression"       = "SNAPPY"
    "projection.enabled"        = "true"
    "storage.location.template" = "${local.bucket_uri}/${local.iot_prefix[each.key]}/device_id=$${device_id}/date=$${date}/"
  })

  partition_keys {
    name = "device_id"
    type = "string"
  }
  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.bucket_uri}/${local.iot_prefix[each.key]}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# --- trusted satellite grid (spec 51 §4, #31) ------------------------------
# One Parquet per batch from WriteTrusted (cloud/satellite/pipeline):
#   trusted/satellite_grid/period_type=<scene|season>/year=<YYYY>/<period>.parquet
# Must match satellite_schema.SATELLITE_GRID / SATELLITE_GRID_PARTITIONS in
# cloud/pipeline/common/satellite_schema.py (checked by tests/test_catalog_schema.py).
locals {
  satellite_grid_partitions = [
    ["period_type", "string"],
    ["year", "int"],
  ]
  satellite_grid_columns = [
    ["cell_id", "string"],
    ["period", "string"],
    ["date_start", "date"],
    ["date_end", "date"],
    ["n_scenes", "int"],
    ["clear_obs_mean", "double"],
    ["valid_pixel_ratio", "double"],
    ["lst_mean", "double"],
    ["lst_p90", "double"],
    ["ndvi_mean", "double"],
    ["ndbi_mean", "double"],
    ["green_ratio", "double"],
  ]
}

resource "aws_glue_catalog_table" "satellite_grid" {
  database_name = aws_glue_catalog_database.this.name
  name          = "satellite_grid"
  description   = "Landsat LST/NDVI/NDBI per 90 m cell, one file per scene/season (trusted/satellite_grid/, spec 51)"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"                = "parquet"
    "parquet.compression"           = "SNAPPY"
    "projection.enabled"            = "true"
    "projection.period_type.type"   = "enum"
    "projection.period_type.values" = "scene,season"
    "projection.year.type"          = "integer"
    "projection.year.range"         = var.satellite_year_range
    "storage.location.template"     = "${local.bucket_uri}/trusted/satellite_grid/period_type=$${period_type}/year=$${year}/"
  }

  dynamic "partition_keys" {
    for_each = local.satellite_grid_partitions
    content {
      name = partition_keys.value[0]
      type = partition_keys.value[1]
    }
  }

  storage_descriptor {
    location      = "${local.bucket_uri}/trusted/satellite_grid/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    dynamic "columns" {
      for_each = local.satellite_grid_columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# --- trusted buildings grid (spec 51 §4, read by curated-merge #32) ---------
#   trusted/buildings_grid/year=<YYYY>/buildings_<YYYY>.parquet
# Must match satellite_schema.BUILDINGS_GRID / BUILDINGS_GRID_PARTITIONS.
locals {
  buildings_grid_partitions = [
    ["year", "int"],
  ]
  buildings_grid_columns = [
    ["cell_id", "string"],
    ["builtup_pct", "double"],
    ["building_count", "int"],
    ["floors_mean", "double"],
    ["far", "double"],
    ["in_cover", "boolean"],
    ["bldg_source", "string"],
  ]
}

resource "aws_glue_catalog_table" "buildings_grid" {
  database_name = aws_glue_catalog_database.this.name
  name          = "buildings_grid"
  description   = "Building summary per 90 m cell, one file per snapshot year (trusted/buildings_grid/, spec 51). in_cover=false = no data, not 'no buildings'"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"            = "parquet"
    "parquet.compression"       = "SNAPPY"
    "projection.enabled"        = "true"
    "projection.year.type"      = "integer"
    "projection.year.range"     = var.satellite_year_range
    "storage.location.template" = "${local.bucket_uri}/trusted/buildings_grid/year=$${year}/"
  }

  dynamic "partition_keys" {
    for_each = local.buildings_grid_partitions
    content {
      name = partition_keys.value[0]
      type = partition_keys.value[1]
    }
  }

  storage_descriptor {
    location      = "${local.bucket_uri}/trusted/buildings_grid/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }

    dynamic "columns" {
      for_each = local.buildings_grid_columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# --- Athena ----------------------------------------------------------------
resource "aws_athena_workgroup" "this" {
  name          = var.workgroup_name
  description   = "IoT walking queries (spec 40); results expire after 7 days (storage lifecycle)"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "${local.bucket_uri}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# Example queries, visible in the Athena console under "Saved queries".
locals {
  named_queries = {
    "readings-1min-one-day"    = "1 device x 1 day of readings_1min (done-check of #17: <= 5 s)"
    "readings-1s-one-hour"     = "1 device x 1 hour of readings_1s"
    "quality-per-day"          = "Loss rate per device per day"
    "raw-rows-per-hour"        = "Raw CSV rows per hour vs trusted rows (pipeline check)"
    "raw-unknown-time-count"   = "Rows without a trusted time per device / session (RTC problems)"
    "satellite-grid-per-batch" = "satellite_grid cells / LST per scene or season (done-check of #31)"
    "cell-period-coverage"     = "cell_period rows and column coverage per period / match set (done-check of #32)"
  }
}

resource "aws_athena_named_query" "this" {
  for_each = local.named_queries

  name        = each.key
  description = each.value
  workgroup   = aws_athena_workgroup.this.id
  database    = aws_glue_catalog_database.this.name
  query       = file("${path.module}/queries/${each.key}.sql")
}

output "database_name" {
  value = aws_glue_catalog_database.this.name
}

output "workgroup_name" {
  value = aws_athena_workgroup.this.name
}

output "results_location" {
  value = "${local.bucket_uri}/athena-results/"
}

output "device_ids" {
  value = local.device_ids
}
