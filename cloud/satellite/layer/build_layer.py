"""Build gee-layer.zip for AWS Lambda (python3.12, x86_64).

Works on Windows/macOS/Linux: pip downloads manylinux wheels for the Lambda
platform instead of wheels for this machine.

    python cloud/satellite/layer/build_layer.py
    -> cloud/satellite/layer/build/gee-layer.zip  (build/ is gitignored)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
SITE = BUILD / "python"  # Lambda adds /opt/python to sys.path
ZIP_BASE = BUILD / "gee-layer"
MAX_UNZIPPED_MB = 250  # Lambda limit for function + all layers


def main() -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    SITE.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "-r", str(HERE / "requirements.txt"),
            "--target", str(SITE),
            "--platform", "manylinux2014_x86_64",
            "--implementation", "cp",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "--no-compile",
            "--quiet",
        ],
        check=True,
    )
    for cache in SITE.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    size_mb = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file()) / 1e6
    out = shutil.make_archive(str(ZIP_BASE), "zip", root_dir=BUILD, base_dir="python")
    zip_mb = Path(out).stat().st_size / 1e6
    print(f"{out}: {zip_mb:.1f} MB zipped, {size_mb:.1f} MB unzipped")
    if size_mb > MAX_UNZIPPED_MB:
        sys.exit(f"unzipped size exceeds Lambda limit {MAX_UNZIPPED_MB} MB")
    if zip_mb > 50:
        print("warning: > 50 MB, direct upload fails; upload the zip to S3 instead")


if __name__ == "__main__":
    main()
