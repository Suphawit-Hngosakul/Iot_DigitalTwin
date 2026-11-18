"""Glue tables (cloud/infra/modules/catalog, #17) must match schema.py.

Parquet columns come from schema.TABLES, raw CSV columns from schema.CSV_COLUMNS.
If one side changes without the other, Athena reads wrong/NULL columns.
"""

import re
from pathlib import Path

from common import schema

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


def test_partition_keys_not_in_columns():
    text = CATALOG_TF.read_text(encoding="utf-8")
    keys = {k for k, _ in schema.PARTITION_KEYS} | {"hour"}
    for start in ["raw_columns = [", *(f"{t} = [" for t in schema.TABLES)]:
        assert not keys & {n for n, _ in _columns(text, start)}, start
