"""Make a device token and its sha256 hash for DEVICE_TOKENS.

    python cloud/ingest/make_token.py IOTW-A001

Put the token in the device's secrets.h (and .env for the simulator); put only
the hash in terraform.tfvars (`device_token_hashes`). Never commit either.
"""

import hashlib
import secrets
import sys


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: make_token.py <device_id>")
    device_id = sys.argv[1]
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    print(f"device_id : {device_id}")
    print(f"token     : {token}      # -> secrets.h / .env only")
    print(f"tfvars    : \"{digest}\" = \"{device_id}\"")


if __name__ == "__main__":
    main()
