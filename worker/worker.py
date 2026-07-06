"""SysEval worker: claim → evaluate → report, forever.

The worker is deliberately DUMB and stateless. If it dies mid-job, nothing
of value dies with it — the authoritative record of who-was-doing-what
lives in the coordinator's storage. Dumb workers are what make "just kill
it and start another one" a legitimate recovery strategy (Week 3).

Week-2 honesty note: if this process dies mid-job, the job is stranded in
"running" forever, because nothing yet notices worker death. That is the
cliffhanger that motivates heartbeats.
"""
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import requests

# Make `evaluator` importable when run as a plain script from anywhere,
# without turning the repo into an installed package this early.
sys.path.append(str(Path(__file__).resolve().parents[1]))
from evaluator.pipeline import evaluate  # noqa: E402

COORD = os.environ.get("COORDINATOR_URL", "http://localhost:8000")
# hostname-pid: unique enough for one machine, and human-readable in logs —
# you can tell WHICH worker died. Week 3 leans on this id for heartbeats.
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
POLL_S = 1.0  # polling beats push here: workers stay connectionless and
              # disposable; 1 req/s of overhead is nothing at our scale


def run_one() -> bool:
    """Claim and run a single job. Returns False if the queue was empty."""
    resp = requests.post(f"{COORD}/jobs/claim",
                         json={"worker_id": WORKER_ID}, timeout=10)
    if resp.status_code == 204:
        return False
    job = resp.json()
    print(f"[{WORKER_ID}] claimed {job['job_id']} ({job['task_id']})", flush=True)

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{job['task_id']}.c"
        src.write_text(job["source_code"])
        result = evaluate(src)  # minutes of docker work happens here

    resp = requests.post(f"{COORD}/jobs/{job['job_id']}/result",
                         json={"worker_id": WORKER_ID,
                               "result": result.to_dict()},
                         timeout=10)
    resp.raise_for_status()
    print(f"[{WORKER_ID}] finished {job['job_id']}: {result.verdict.value}",
          flush=True)
    return True


def main() -> None:
    print(f"[{WORKER_ID}] online, coordinator = {COORD}", flush=True)
    while True:
        try:
            if not run_one():
                time.sleep(POLL_S)
        except requests.RequestException as exc:
            # Coordinator down or flaky network: keep retrying, never die.
            # A worker that exits on transient errors turns a 5-second
            # blip into a permanently smaller cluster.
            print(f"[{WORKER_ID}] coordinator unreachable ({exc}); retrying",
                  flush=True)
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
