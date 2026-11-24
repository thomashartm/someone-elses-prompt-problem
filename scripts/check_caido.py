"""Check the configured Caido instance using the CLI. Never send a target request."""
import os
import re
import subprocess
import sys


def ready():
    url = os.environ.get("CAIDO_URL") or "http://127.0.0.1:8080"
    try:
        status = subprocess.run(
            ["caido-cli", "--url", url, "status"], capture_output=True,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # v4.5.0 also exits zero on authentication and connection failures.
    return status.returncode == 0 and all(
        re.search(rf"^{field}: OK(?: \([^\r\n]*\))?\r?$", status.stdout, re.MULTILINE)
        for field in ("Auth", "Instance")
    )


if __name__ == "__main__":
    if not ready():
        sys.exit("Caido unavailable: caido-cli status must report Auth: OK and Instance: OK.")
