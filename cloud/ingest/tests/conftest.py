import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
# handler.py is in cloud/ingest; `common` lives in cloud/pipeline (Terraform
# zips it next to handler.py, so in Lambda it is a top-level package too).
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[2] / "pipeline"))

# moto needs fake credentials so nothing can reach a real account.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
