"""Read and judge one batch (spec 30 §4) — shared by Validate, Transform, Verify.

Pure Python (csv + schema.py), no pandas. ValidateBatch and TransformBatch both
call `evaluate()` on the same raw files, so the row decisions are identical
without passing per-row flags through Step Functions (256 KB state limit).

Every raw data line ends in exactly one bucket:
  kept       -> readings_1s
  duplicate  -> same (Device_ID, Session, Record_ID) seen earlier in the batch
  invalid    -> any other DROP flag (PARSE_ERROR, NO_RTC, OUT_OF_BATCH_HOUR, ...)
so  raw_rows == kept + duplicate + invalid  (what VerifyBatch checks). TransformBatch
writes the invalid and duplicate rows to quarantine/rows|duplicate/iot/ (rows_csv).
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from common import schema

MAX_INVALID_RATIO = 0.20
TIME_JUMP_SLACK_S = 5          # spec 30: jump > 5 s within a session
BAD_SAMPLE_LIMIT = 20


@dataclass
class RawFile:
    key: str
    header: list[str]
    rows: list[dict]           # csv.DictReader rows, in file order


@dataclass
class EvalRow:
    key: str
    line: int                  # 1-based line in its file (header = 1)
    raw: dict
    row: dict | None           # schema.parse_row output, None on PARSE_ERROR
    flags: set[str]
    error: str | None = None
    bucket: str = "kept"       # kept | duplicate | invalid

    @property
    def session(self) -> int | None:
        if self.row is not None:
            return self.row["Session"]
        try:
            return int((self.raw.get("Session") or "").strip())
        except ValueError:
            return None


@dataclass
class BatchEval:
    batch_id: str
    batch: schema.BatchId
    files: list[RawFile]
    rows: list[EvalRow]
    fail_reasons: list[str] = field(default_factory=list)

    @property
    def raw_rows(self) -> int:
        return len(self.rows)

    def count(self, bucket: str) -> int:
        return sum(1 for r in self.rows if r.bucket == bucket)

    @property
    def invalid_ratio(self) -> float:
        base = self.raw_rows - self.count("duplicate")
        return self.count("invalid") / base if base else 0.0

    @property
    def status(self) -> str:
        return "FAIL" if self.fail_reasons else "PASS"

    def kept(self) -> list[EvalRow]:
        return [r for r in self.rows if r.bucket == "kept"]

    def sessions(self) -> list[dict]:
        """Per-session counts for quality / report (session None = unparseable)."""
        by: dict = defaultdict(list)
        for r in self.rows:
            by[r.session].append(r)
        out = []
        for session in sorted(by, key=lambda s: (s is None, s or 0)):
            rows = by[session]
            rids = sorted({r.row["Record_ID"] for r in rows
                           if r.row is not None and r.bucket != "duplicate"})
            expected = rids[-1] - rids[0] + 1 if rids else 0
            missing_ranges = []
            for a, b in zip(rids, rids[1:]):
                if b - a > 1:
                    missing_ranges.append([a + 1, b - 1])
            missing = expected - len(rids)
            flag_counts = Counter(f for r in rows for f in r.flags)
            out.append({
                "session": session,
                "expected_rows": expected,
                "received_rows": len(rids),
                "missing_rows": missing,
                "missing_ranges": missing_ranges,
                "duplicate_rows": sum(1 for r in rows if r.bucket == "duplicate"),
                "invalid_rows": sum(1 for r in rows if r.bucket == "invalid"),
                "loss_rate": (missing / expected) if expected else 0.0,
                "flag_counts": dict(sorted(flag_counts.items())),
            })
        return out


def read_files(s3, bucket: str, manifest: list[dict]) -> list[RawFile]:
    files = []
    for f in sorted(manifest, key=lambda f: f["key"]):
        body = s3.get_object(Bucket=bucket, Key=f["key"])["Body"].read()
        files.append(parse_file(f["key"], body))
    return files


def parse_file(key: str, body: bytes) -> RawFile:
    text = body.decode("utf-8", errors="replace").lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return RawFile(key, [], [])
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return RawFile(key, list(reader.fieldnames or []), list(reader))


def count_data_lines(body: bytes) -> int:
    """Non-empty lines minus the header (VerifyBatch's independent raw count)."""
    text = body.decode("utf-8", errors="replace")
    n = sum(1 for ln in text.splitlines() if ln.strip())
    return max(n - 1, 0)


def evaluate(batch_id: str, files: list[RawFile], devices) -> BatchEval:
    b = schema.parse_batch_id(batch_id)
    start = b.start_local
    ev = BatchEval(batch_id, b, files, [])

    # ---- header (batch-level) ------------------------------------------------
    for f in files:
        problems = schema.check_header(f.header)
        if problems:
            ev.fail_reasons.append(f"BAD_HEADER {f.key}: {'; '.join(problems)}")

    # ---- per row --------------------------------------------------------------
    seen: set[tuple] = set()
    for f in files:
        for i, raw in enumerate(f.rows, start=2):
            try:
                row = schema.parse_row(raw)
            except schema.RowError as e:
                ev.rows.append(EvalRow(f.key, i, raw, None, {"PARSE_ERROR"}, str(e)))
                continue
            flags = schema.check_row(row, batch_hour=start)
            key = (row["Device_ID"], row["Session"], row["Record_ID"])
            if key in seen:
                flags.add("DUPLICATE")
            seen.add(key)
            ev.rows.append(EvalRow(f.key, i, raw, row, flags))

    _flag_time_jumps(ev.rows)

    for r in ev.rows:
        if "DUPLICATE" in r.flags:
            r.bucket = "duplicate"
        elif schema.should_drop(r.flags):
            r.bucket = "invalid"

    # ---- batch-level checks ---------------------------------------------------
    parsed = [r for r in ev.rows if r.row is not None]
    wrong_id = {r.row["Device_ID"] for r in parsed} - {b.device_id}
    if wrong_id:
        ev.fail_reasons.append(f"DEVICE_ID_MISMATCH path={b.device_id} rows={sorted(wrong_id)}")

    reg = schema.lookup_device(devices, b.device_id, start) or \
        schema.lookup_device(devices, b.device_id, start + timedelta(minutes=59, seconds=59))
    if reg is None:
        ev.fail_reasons.append(f"UNKNOWN_DEVICE {b.device_id} not in devices.csv for {start:%Y-%m-%d %H}:00")
    else:
        types = {r.row["Device_Type"] for r in parsed} - {reg.device_type}
        if types:
            ev.fail_reasons.append(
                f"DEVICE_TYPE_MISMATCH registry={reg.device_type} rows={sorted(types)}")
        if reg.device_type == "Stationary":
            outside = [r for r in ev.kept()
                       if schema.lookup_device(devices, b.device_id, r.row["ts_local"]) is None]
            if outside:
                ev.fail_reasons.append(
                    f"UNKNOWN_STATION_SITE {len(outside)} rows outside registry validity")

    if ev.raw_rows and ev.invalid_ratio > MAX_INVALID_RATIO:
        ev.fail_reasons.append(
            f"TOO_MANY_INVALID {ev.count('invalid')}/{ev.raw_rows - ev.count('duplicate')} "
            f"= {ev.invalid_ratio:.0%} > {MAX_INVALID_RATIO:.0%}")
    if ev.raw_rows == 0 and not ev.fail_reasons:
        ev.fail_reasons.append("EMPTY_BATCH files have a header but no rows")
    return ev


def rows_csv(ev: BatchEval, bucket: str) -> bytes | None:
    """Rows of one bucket ("invalid" | "duplicate") as CSV for quarantine/, or None.

    Columns: source_file, line, reason, then the raw CSV columns exactly as the
    device wrote them. reason = PARSE_ERROR text or the sorted flags of the row.
    """
    rows = [r for r in ev.rows if r.bucket == bucket]
    if not rows:
        return None
    header = next((f.header for f in ev.files if f.header), [])
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["source_file", "line", "reason", *header])
    for r in rows:
        reason = r.error or ",".join(sorted(r.flags))
        w.writerow([r.key, r.line, reason, *[r.raw.get(c, "") for c in header]])
    return out.getvalue().encode("utf-8")


def _flag_time_jumps(rows: list[EvalRow]) -> None:
    """TIME_JUMP: time goes back, or advances more than (Record_ID gap + 5 s)."""
    by_session: dict = defaultdict(list)
    for r in rows:
        if r.row is not None and r.row["ts_local"] is not None and "DUPLICATE" not in r.flags:
            by_session[r.row["Session"]].append(r)
    for rs in by_session.values():
        rs.sort(key=lambda r: r.row["Record_ID"])
        for prev, cur in zip(rs, rs[1:]):
            dt = (cur.row["ts_local"] - prev.row["ts_local"]).total_seconds()
            step = cur.row["Record_ID"] - prev.row["Record_ID"]
            if dt < 0 or dt > step + TIME_JUMP_SLACK_S:
                cur.flags.add("TIME_JUMP")


def report(ev: BatchEval, generated_at: datetime) -> dict:
    """validation/<batch_id>/report.json — readable by a person."""
    flag_counts = Counter(f for r in ev.rows for f in r.flags)
    bad = [r for r in ev.rows if r.bucket != "kept"][:BAD_SAMPLE_LIMIT]
    return {
        "batch_id": ev.batch_id,
        "status": ev.status,
        "fail_reasons": ev.fail_reasons,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "files": [{"key": f.key, "rows": len(f.rows),
                   "header_ok": not schema.check_header(f.header)} for f in ev.files],
        "raw_rows": ev.raw_rows,
        "kept_rows": ev.count("kept"),
        "duplicate_rows": ev.count("duplicate"),
        "invalid_rows": ev.count("invalid"),
        "invalid_ratio": round(ev.invalid_ratio, 4),
        "max_invalid_ratio": MAX_INVALID_RATIO,
        "flag_counts": dict(sorted(flag_counts.items())),
        "sessions": ev.sessions(),
        "bad_row_samples": [{
            "key": r.key, "line": r.line,
            "session": r.session,
            "record_id": r.row["Record_ID"] if r.row else r.raw.get("Record_ID"),
            "datetime": r.raw.get("DateTime"),
            "bucket": r.bucket,
            "flags": sorted(r.flags),
            "error": r.error,
        } for r in bad],
    }
