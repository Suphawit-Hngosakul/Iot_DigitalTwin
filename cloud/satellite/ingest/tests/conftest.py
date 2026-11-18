import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # cloud/satellite/ingest (handler.py, ingest.py)

# The tests never call Earth Engine: stub the layer-only packages so ingest.py imports.
sys.modules.setdefault("ee", types.ModuleType("ee"))
try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")
try:
    from google.auth import aws  # noqa: F401
except ImportError:
    google = sys.modules.setdefault("google", types.ModuleType("google"))
    google.auth = sys.modules.setdefault("google.auth", types.ModuleType("google.auth"))
    google.auth.aws = sys.modules.setdefault("google.auth.aws", types.ModuleType("google.auth.aws"))

# Fake credentials so nothing can ever reach a real account.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
