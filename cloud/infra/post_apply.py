#!/usr/bin/env python3
"""Fill a freshly applied stack with data (plan R8). Run from anywhere after `terraform apply`.

Steps (in this order, each can be run alone; all are safe to repeat):
  iot        upload the legacy IoT logs (iot/tools/converted/*.csv + any already-converted
             CSVs given with --iot-files) through the ingest API. Ingest writes a pending
             marker per hour, so one pipeline run picks up every hour however old; the
             script starts that run and waits for it.
  satellite  backfill Landsat scenes with satellite_ingest one month per call (Lambda max
             900 s): every month from --since (default 2025-01) to today, plus the hot
             season (SEASON_MONTHS) of each year from --season-from (default 2024) before
             --since, so those years get their season composite too. Then the building grid. Every finished
             batch starts the satellite pipeline by itself (S3 _COMPLETE rule); the script
             waits until none is running. The curated-merge and analytics triggers are
             switched off meanwhile (one merge instead of one per scene) and always
             switched back on at the end.
  merge      one curated-merge run over every stale period, then one analytics run.

Usage:
  python cloud/infra/post_apply.py --all
  python cloud/infra/post_apply.py iot --iot-files iot/tools/raw/tu_20261120/*.csv
  python cloud/infra/post_apply.py satellite merge --since 2025-01-01 --dry-run

The device token is read from IOT_DEVICE_TOKEN or asked for (never printed, never put on
a command line). AWS credentials and region come from the normal AWS CLI profile; the
region also falls back to the one in the state machine ARNs.
"""
from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

INFRA = Path(__file__).resolve().parent
REPO = INFRA.parents[1]
STEPS = ("iot", "satellite", "merge")
WINDOW_MONTHS = 1
SEASON_MONTHS = (3, 5)   # = SEASON_MONTHS in cloud/satellite/ingest/ingest.py
POLL_S = 20


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def tf_outputs() -> dict:
    out = subprocess.run(["terraform", f"-chdir={INFRA}", "output", "-json"],
                         check=True, capture_output=True, text=True).stdout
    return {k: v.get("value") for k, v in json.loads(out).items()}


def satellite_ranges(since: date, season_from: int, until: date) -> list[tuple[str, str]]:
    """Hot-season months of season_from .. since.year-1, then every month since -> until."""
    m0, m1 = SEASON_MONTHS
    out = []
    for y in range(season_from, since.year + 1):
        start, end = date(y, m0, 1), add_months(date(y, m1, 1), 1)
        end = min(end, since)
        if start < end:
            out += windows(start, end)
    return out + windows(since, until)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def windows(since: date, until: date, months: int = WINDOW_MONTHS) -> list[tuple[str, str]]:
    """[start, end) ranges of `months` calendar months from since's month up to until."""
    out, start = [], date(since.year, since.month, 1)
    start = max(start, since)
    while start < until:
        end = min(add_months(date(start.year, start.month, 1), months), until)
        out.append((start.isoformat(), end.isoformat()))
        start = end
    return out


# ---- AWS helpers -------------------------------------------------------------------------

class Aws:
    def __init__(self, region: str | None, dry_run: bool):
        import boto3
        from botocore.config import Config
        self.dry = dry_run
        self.sfn = boto3.client("stepfunctions", region_name=region)
        self.events = boto3.client("events", region_name=region)
        # satellite_ingest runs up to 900 s per call
        self.lam = boto3.client("lambda", region_name=region, config=Config(
            read_timeout=960, connect_timeout=10, retries={"max_attempts": 0}))

    def start_and_wait(self, sm_arn: str, payload: dict, label: str) -> str:
        if self.dry:
            log(f"[dry-run] start {label}: {json.dumps(payload)}")
            return "DRY_RUN"
        arn = self.sfn.start_execution(stateMachineArn=sm_arn, input=json.dumps(payload))["executionArn"]
        log(f"{label}: started {arn.rsplit(':', 1)[-1]}")
        while True:
            d = self.sfn.describe_execution(executionArn=arn)
            if d["status"] != "RUNNING":
                log(f"{label}: {d['status']}" + (f" ({d.get('error')}: {d.get('cause', '')[:300]})"
                                                if d["status"] != "SUCCEEDED" else ""))
                return d["status"]
            time.sleep(POLL_S)

    def wait_idle(self, sm_arn: str, label: str, settle_s: int = 60) -> None:
        """Until no execution is RUNNING for settle_s (new ones may still be starting)."""
        if self.dry:
            return
        idle_since = None
        while True:
            running = self.sfn.list_executions(stateMachineArn=sm_arn, statusFilter="RUNNING",
                                               maxResults=100)["executions"]
            if running:
                idle_since = None
                log(f"{label}: {len(running)} running")
            else:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= settle_s:
                    break
            time.sleep(POLL_S)
        failed = self.sfn.list_executions(stateMachineArn=sm_arn, statusFilter="FAILED",
                                          maxResults=100)["executions"]
        if failed:
            log(f"{label}: {len(failed)} failed execution(s) in history, check the console")

    def invoke(self, fn: str, payload: dict, label: str) -> bool:
        if self.dry:
            log(f"[dry-run] invoke {fn}: {json.dumps(payload)}")
            return True
        r = self.lam.invoke(FunctionName=fn, Payload=json.dumps(payload).encode())
        body = r["Payload"].read().decode(errors="replace")
        if r.get("FunctionError"):
            log(f"{label}: ERROR {body[:500]}")
            return False
        try:
            res = json.loads(body)
            scenes = res.get("processed_scenes") or []
            summary = {"dates": res.get("total_dates_found"),
                       "new": sum(r.get("status") not in ("already_exists", "already_skipped")
                                  for r in scenes),
                       "errors": res.get("errors")} if "processed_scenes" in res else \
                {k: res.get(k) for k in ("status", "result")}
        except (ValueError, TypeError):
            summary = body[:200]
        log(f"{label}: ok {summary}")
        return True

    def set_rules(self, names: list[str], enabled: bool) -> None:
        for n in names:
            if self.dry:
                log(f"[dry-run] {'enable' if enabled else 'disable'} rule {n}")
                continue
            try:
                (self.events.enable_rule if enabled else self.events.disable_rule)(Name=n)
                log(f"rule {n}: {'ENABLED' if enabled else 'DISABLED'}")
            except self.events.exceptions.ResourceNotFoundException:
                pass


# ---- steps -------------------------------------------------------------------------------

def step_iot(aws: Aws, out: dict, a) -> None:
    sys.path.insert(0, str(REPO / "iot" / "tools"))
    import legacy_log

    files = sorted(glob.glob(str(REPO / "iot" / "tools" / "converted" / "*.csv")))
    for pat in a.iot_files:
        files += sorted(glob.glob(pat))
    files = list(dict.fromkeys(files))
    if not files:
        log("iot: no CSV to upload, skipped")
        return
    token = "" if a.dry_run else (os.environ.get("IOT_DEVICE_TOKEN")
                                  or getpass.getpass("device token (not shown): ").strip())
    log(f"iot: uploading {len(files)} file(s) to {out['ingest_url']}")
    try:
        legacy_log.cmd_upload(SimpleNamespace(files=files, url=out["ingest_url"], token=token,
                                              timeout=30, dry_run=a.dry_run))
    except SystemExit as e:
        if e.code:
            raise SystemExit("iot: some chunks failed (see ERR lines); fix and run `iot` again")
    # no batch_ids: the run plans every hour that has a pending marker
    st = aws.start_and_wait(out["pipeline_state_machine_arn"], {"mode": "normal"}, "iot pipeline")
    if st not in ("SUCCEEDED", "DRY_RUN"):
        raise SystemExit("iot pipeline did not succeed; check the execution in the console")


def step_satellite(aws: Aws, out: dict, a) -> None:
    fn = out.get("satellite_ingest_function_name")
    sm = out.get("satellite_pipeline_state_machine_arn")
    if not fn or not sm:
        log("satellite: satellite_enabled / satellite_pipeline_enabled is off, skipped")
        return
    ranges = satellite_ranges(date.fromisoformat(a.since), a.season_from,
                              date.today() + timedelta(days=1))
    log(f"satellite: {len(ranges)} month(s): seasons from {a.season_from}, all months from "
        f"{a.since}; GEE errors usually mean the WIF "
        f"grant for {out.get('satellite_wif_role')} is missing")
    bad = 0
    for start, end in ranges:
        bad += not aws.invoke(fn, {"mode": "ingest", "start_date": start, "end_date": end},
                              f"ingest {start}..{end}")
    bad += not aws.invoke(fn, {"mode": "buildings"}, "buildings")
    aws.wait_idle(sm, "satellite pipeline")
    if bad:
        log(f"satellite: {bad} call(s) failed; rerun `satellite` (finished scenes are skipped)")


def step_merge(aws: Aws, out: dict) -> None:
    merge, analytics = out.get("curated_merge_state_machine_arn"), out.get("analytics_state_machine_arn")
    if merge:
        aws.wait_idle(merge, "curated-merge", settle_s=0)
        aws.start_and_wait(merge, {}, "curated-merge")
    if analytics:
        aws.wait_idle(analytics, "analytics", settle_s=0)
        aws.start_and_wait(analytics, {}, "analytics")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("steps", nargs="*", help="any of: iot satellite merge (or use --all)")
    ap.add_argument("--all", action="store_true", help="iot, satellite, merge")
    ap.add_argument("--since", default="2025-01-01", help="satellite: every month from this date")
    ap.add_argument("--season-from", type=int, default=2024,
                    help="satellite: hot-season months only, from this year until --since")
    ap.add_argument("--iot-files", nargs="*", default=[], help="extra CSVs already in the current schema")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    ap.add_argument("--dry-run", action="store_true", help="print what would be done, change nothing")
    a = ap.parse_args()
    unknown = [s for s in a.steps if s not in STEPS]
    if unknown:
        ap.error(f"unknown step(s): {unknown}; choose from {STEPS}")
    steps = list(STEPS) if a.all else [s for s in STEPS if s in a.steps]
    if not steps:
        ap.error("choose steps (iot satellite merge) or --all")

    out = tf_outputs()
    if not out.get("pipeline_state_machine_arn"):
        ap.error("no terraform outputs here: run `terraform apply` in cloud/infra first")
    region = a.region or out["pipeline_state_machine_arn"].split(":")[3]
    aws = Aws(region, a.dry_run)
    prefix = (out.get("satellite_ingest_function_name") or "iot-dt-satellite-ingest").removesuffix("-satellite-ingest")
    rules = [f"{prefix}-curated-published", f"{prefix}-analytics-merge-published"]
    log(f"region {region}, steps {steps}{' (dry run)' if a.dry_run else ''}")

    if "iot" in steps:
        step_iot(aws, out, a)
    if "satellite" in steps:
        aws.set_rules(rules, enabled=False)
        try:
            step_satellite(aws, out, a)
        finally:
            aws.set_rules(rules, enabled=True)
    if "merge" in steps:
        step_merge(aws, out)
    log("done")


if __name__ == "__main__":
    main()
