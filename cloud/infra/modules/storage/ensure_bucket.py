#!/usr/bin/env python3
"""Create the S3 data bucket if it does not exist (run by modules/storage on apply).

    python ensure_bucket.py --region ap-southeast-2 <bucket>

Uses the AWS CLI (same credentials as Terraform), so no shell or boto3 is
needed: works the same on Windows, macOS and Linux. Idempotent: an existing
bucket is left as it is.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bucket")
    ap.add_argument("--region", required=True)
    a = ap.parse_args()

    aws = shutil.which("aws")
    if not aws:
        print("'aws' (AWS CLI) not found on PATH", file=sys.stderr)
        return 1

    head = subprocess.run([aws, "s3api", "head-bucket", "--bucket", a.bucket],
                          capture_output=True, text=True)
    if head.returncode == 0:
        print(f"bucket {a.bucket} exists")
        return 0

    cmd = [aws, "s3api", "create-bucket", "--bucket", a.bucket, "--region", a.region]
    if a.region != "us-east-1":  # us-east-1 rejects an explicit LocationConstraint
        cmd += ["--create-bucket-configuration", f"LocationConstraint={a.region}"]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
