import os
import sys
from pathlib import Path

# `common` lives in cloud/pipeline/ (shared; Terraform zips it next to handler.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipeline"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
