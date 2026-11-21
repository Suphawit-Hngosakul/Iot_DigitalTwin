# cloud/satellite/pipeline — satellite pipeline (#30, #31, spec 51)

Step Functions `iot-dt-sat-pipeline` (one execution = one batch) reads one
batch that `satellite_ingest` wrote to `raw/satellite/` and turns it into
per-cell 90 m data plus map layers. Zones follow D23 (raw → trusted → curated + quarantine).

```
raw/satellite/<batch>/_COMPLETE  ──(S3 → EventBridge rule iot-dt-sat-complete)──►
CheckBatch ─► AlreadyProcessed? ─► ValidateBatch ─► IsValid? ─► WriteTrusted ─► RenderLayers ─► VerifyBatch ─► Published
                 │ NO_DATA / ALREADY_PROCESSED → Succeed        │ FAIL → QuarantineBatch → Succeed
                 └ errors anywhere → NotifyFailure (SNS iot-dt-iot-pipeline-alerts) → PipelineFailed
```

batch_id = folder name: `scene_<YYYY-MM-DD>`, `season_<YYYY>_03-05`, and `static_buildings` (folder `static=buildings/`)

| state | Lambda | what it does |
|---|---|---|
| CheckBatch | `iot-dt-sat-check-batch` | manifest (key, etag, size) + hash; `NO_DATA` if `_COMPLETE` is missing or the date is in `skipped/`; `ALREADY_PROCESSED` if the hash equals the one in `curated/_success/` |
| ValidateBatch | `iot-dt-sat-validate-batch` | runs the contract (`cloud/pipeline/contracts/satellite_scene|satellite_season|buildings.yaml`); writes `validation/<source>/<batch>/report.json`, bad rows → `quarantine/rows/`, duplicate keys → `quarantine/duplicate/` |
| QuarantineBatch | `iot-dt-sat-quarantine-batch` | a batch that fails a BLOCK batch check: copy the raw files + `reason.json` → `quarantine/batch/<source>/<batch>/` |
| WriteTrusted | `iot-dt-sat-write-trusted` | 30 m pixels → 90 m cells (3×3, no resampling, D11) → `trusted/satellite_grid/period_type=<t>/year=<y>/<period>.parquet` / `trusted/buildings_grid/year=<y>/buildings_<y>.parquet` |
| RenderLayers | `iot-dt-sat-render-layers` | PNG + JSON (corners for the MapLibre image source + legend) → `curated/satellite/layers/<batch>/{lst,ndvi,ndbi}` / `layers/static/building_density` (fixed colour scales so every scene compares) |
| VerifyBatch | `iot-dt-sat-verify-batch` | raw unchanged, rows add up, parquet rows = AOI cells, mean LST of cells vs pixels ≤ 0.5 °C, layer files present → write `curated/_success/<source>/<batch>.json` |

Each step re-reads raw and re-runs the contract instead of passing rows through
Step Functions (256 KB limit). The result is the same every time.

`bands.tif` is not read at all, and no output sends AOI boundary lines out (D17).

## Deploy

```hcl
# cloud/infra/terraform.tfvars
satellite_pipeline_enabled = true
# satellite_pipeline_trigger_enabled = false   # manual runs only
```

Terraform converts `contracts/*.yaml` to JSON in the zip (the Lambda runtime has no PyYAML).
`write_trusted` and `verify_batch` use the pandas layer (pyarrow).

## Manual run / backfill

```bash
SM=$(terraform -chdir=cloud/infra output -raw satellite_pipeline_state_machine_arn)
aws stepfunctions start-execution --state-machine-arn "$SM" \
  --input '{"batch_id": "scene_2024-04-12", "mode": "normal"}'
# "mode": "force" = rebuild even if the manifest has not changed
```

For a backfill, loop over the batch folders in `raw/satellite/` (one execution per batch).

## Tests

```bash
pip install -r cloud/satellite/pipeline/requirements-dev.txt
python -m pytest -q cloud/pipeline/tests cloud/satellite/pipeline/tests   # moto, no real AWS
```
