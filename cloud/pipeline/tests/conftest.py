import os
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]

# Make `common` importable as a top-level package (cloud/pipeline on sys.path).
sys.path.insert(0, str(PIPELINE))

# moto needs fake credentials so nothing can reach a real account.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

