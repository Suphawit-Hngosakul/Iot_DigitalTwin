import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # cloud/relay (relay.py)
# relay.py reads its config at import; the tests point it at a local fake upstream
os.environ.setdefault("UPSTREAM_URL", "http://127.0.0.1:9/ingest")
