#!/usr/bin/env python3
"""Build the web and apply the whole stack in one go (plan R4/R8).

Terraform lists the files of web/dist when it plans, so the web must be built
before every apply that should ship web changes. This script does, in order:

  1. web      npm ci (only when web/node_modules is missing, or with --npm-ci)
              npm run build -> web/dist (public/data/buildings.geojson is copied
              in if it exists; without it the map shows mock buildings)
  2. init     terraform init (only when .terraform/ is missing, or with --init)
  3. apply    terraform apply (asks for "yes" unless --yes)
  4. outputs  prints the URLs to use next and the follow-up steps

Usage (from anywhere):
  python cloud/infra/deploy.py                 # build + apply, Terraform asks to confirm
  python cloud/infra/deploy.py --skip-web      # infra only (web/dist must already exist)
  python cloud/infra/deploy.py --plan          # build + terraform plan, change nothing
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
REPO = INFRA.parents[1]
WEB = REPO / "web"
OUTPUTS = [
    ("web_url", "web (HTTPS)"),
    ("api_url", "API for the web"),
    ("relay_url", "firmware INGEST_URL (plain HTTP)"),
    ("hub_device_url", "firmware wssUrl"),
    ("relay_instance_id", "relay logs: aws ssm start-session --target <id>"),
    ("satellite_wif_role", "GEE WIF principal (aws_role)"),
]


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"'{name}' not found on PATH; install it first (see cloud/infra/README.md step 0)")
    return path


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(Path(cmd[0]).name if i == 0 else c for i, c in enumerate(cmd))}  (in {cwd.relative_to(REPO)})",
          flush=True)
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode:
        raise SystemExit(f"failed with exit code {r.returncode}")


def build_web(npm_ci: bool) -> None:
    npm = tool("npm")
    if npm_ci or not (WEB / "node_modules").is_dir():
        run([npm, "ci"], WEB)
    if not (WEB / "public" / "data" / "buildings.geojson").is_file():
        print("note: web/public/data/buildings.geojson not found -> the map uses mock buildings "
              "(make it with web/scripts/convert_buildings.py)")
    run([npm, "run", "build"], WEB)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-web", action="store_true", help="do not build the web")
    ap.add_argument("--npm-ci", action="store_true", help="reinstall web dependencies first")
    ap.add_argument("--init", action="store_true", help="run terraform init even if already initialised")
    ap.add_argument("--plan", action="store_true", help="terraform plan instead of apply")
    ap.add_argument("--yes", action="store_true", help="terraform apply -auto-approve")
    a = ap.parse_args()

    tf = tool("terraform")
    tool("aws")      # module storage creates the bucket with the AWS CLI
    if not shutil.which("bash"):
        print("warning: 'bash' not on PATH; module storage needs it on first apply (Windows: Git Bash)")
    if not (INFRA / "terraform.tfvars").is_file():
        raise SystemExit("cloud/infra/terraform.tfvars is missing: copy terraform.tfvars.example and fill it in")

    if not a.skip_web:
        build_web(a.npm_ci)
    elif not (WEB / "dist" / "index.html").is_file():
        raise SystemExit("--skip-web but web/dist is empty: build the web first")

    if a.init or not (INFRA / ".terraform").is_dir():
        run([tf, "init", "-input=false"], INFRA)
    if a.plan:
        run([tf, "plan"], INFRA)
        return
    run([tf, "apply", *(["-auto-approve"] if a.yes else [])], INFRA)

    out = json.loads(subprocess.run([tf, "output", "-json"], cwd=INFRA, check=True,
                                    capture_output=True, text=True).stdout)
    print("\n== outputs ==")
    for key, what in OUTPUTS:
        v = (out.get(key) or {}).get("value")
        if v:
            print(f"{key:20} {v}\n{'':20} ({what})")
    print("""
next:
  1. confirm the AWS emails (pipeline alerts + budget) if this is the first apply
  2. new relay_url / hub_device_url? -> put them in secrets.h and flash the boards
  3. GEE: the satellite_wif_role above must be granted on the Google side
  4. fill the stack with data:  python cloud/infra/post_apply.py --all
  5. check everything:          bash cloud/infra/healthcheck.sh""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
