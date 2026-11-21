"""Glue tables (cloud/infra/modules/catalog, #17) must match schema.py.

Parquet columns come from schema.TABLES, raw CSV columns from schema.CSV_COLUMNS.
If one side changes without the other, Athena reads wrong/NULL columns.
"""

import re
from pathlib import Path

from common import satellite_schema, schema

CATALOG_TF = Path(__file__).resolve().parents[2] / "infra" / "modules" / "catalog" / "main.tf"

_COL = re.compile(r'\[\s*"([a-z0-9_]+)"\s*,\s*"([a-z]+)"\s*\]')


def _block(text: str, start: str) -> str:
    """Text of the [...] list that follows `start`."""
    i = text.index(start)
    i = text.index("[", i)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "[":
            depth += 1
        elif text[j] == "]":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise AssertionError(f"unclosed list after {start!r}")


def _columns(text: str, start: str) -> list[tuple[str, str]]:
    return _COL.findall(_block(text, start))


def test_processed_tables_match_schema():
    text = CATALOG_TF.read_text(encoding="utf-8")
    for table, cols in schema.TABLES.items():
        assert _columns(text, f"{table} = [") == list(cols), table


def test_raw_columns_follow_csv_order():
    text = CATALOG_TF.read_text(encoding="utf-8")
    names = [n for n, _ in _columns(text, "raw_columns = [")]
    expected = [c.lower() for c in schema.CSV_COLUMNS]
    expected[0] = "csv_device_id"   # Device_ID clashes with the device_id partition key
    assert names == expected


def test_satellite_grid_matches_satellite_schema():
    text = CATALOG_TF.read_text(encoding="utf-8")
    assert _columns(text, "satellite_grid_columns = [") == list(satellite_schema.SATELLITE_GRID)
    assert (_columns(text, "satellite_grid_partitions = [")
            == list(satellite_schema.SATELLITE_GRID_PARTITIONS))


def test_satellite_grid_location_matches_writer():
    """storage.location.template must produce the folder WriteTrusted writes to."""
    text = CATALOG_TF.read_text(encoding="utf-8")
    m = re.search(r'/(trusted/satellite_grid/period_type=\$\$\{period_type\}/year=\$\$\{year\}/)"', text)
    assert m, "satellite_grid storage.location.template not found"
    template = m.group(1).replace("$${period_type}", "scene").replace("$${year}", "2024")
    key = satellite_schema.s3_key_satellite_grid("scene_2024-03-17")
    assert key.startswith(template), (template, key)
    season = satellite_schema.s3_key_satellite_grid("season_2023_03-05")
    assert season.startswith("trusted/satellite_grid/period_type=season/year=2023/")
    values = re.search(r'"projection\.period_type\.values"\s*=\s*"([^"]+)"', text).group(1)
    assert set(values.split(",")) == {"scene", "season"}


def test_buildings_grid_matches_satellite_schema():
    text = CATALOG_TF.read_text(encoding="utf-8")
    assert _columns(text, "buildings_grid_columns = [") == list(satellite_schema.BUILDINGS_GRID)
    assert (_columns(text, "buildings_grid_partitions = [")
            == list(satellite_schema.BUILDINGS_GRID_PARTITIONS))
    m = re.search(r'/(trusted/buildings_grid/year=\$\$\{year\}/)"', text)
    assert m, "buildings_grid storage.location.template not found"
    key = satellite_schema.s3_key_buildings_grid(2025)
    assert key.startswith(m.group(1).replace("$${year}", "2025"))


def test_partition_keys_not_in_columns():
    text = CATALOG_TF.read_text(encoding="utf-8")
    keys = {k for k, _ in schema.PARTITION_KEYS} | {"hour"}
    for start in ["raw_columns = [", *(f"{t} = [" for t in schema.TABLES)]:
        assert not keys & {n for n, _ in _columns(text, start)}, start
