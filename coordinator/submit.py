"""Submit C source files to the coordinator as jobs (plumbing, not core)."""
import os
import sys
from pathlib import Path

import requests

COORD = os.environ.get("COORDINATOR_URL", "http://localhost:8000")


def main(paths: list) -> int:
    if not paths:
        print("usage: submit.py file.c [file2.c ...]", file=sys.stderr)
        return 2
    for path in paths:
        p = Path(path)
        resp = requests.post(f"{COORD}/jobs", json={
            "task_id": p.stem,
            "source_code": p.read_text(),
        }, timeout=10)
        resp.raise_for_status()
        print(f"submitted {p.stem}: job {resp.json()['job_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
