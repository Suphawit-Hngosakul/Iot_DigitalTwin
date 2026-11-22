from __future__ import annotations

import sys
from pathlib import Path

ANALYTICS_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ROOT = ANALYTICS_ROOT.parent / "pipeline"

for p in (str(ANALYTICS_ROOT), str(PIPELINE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
