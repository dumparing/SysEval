"""SysEval worker (Week 3: now with a heartbeat).

Still deliberately dumb and stateless — the authoritative record of
who-holds-what lives at the coordinator. New: a daemon thread pings the
coordinator every HEARTBEAT_S seconds with (worker_id, idle/busy, job_id,
epoch). The main thread spends MINUTES blocked inside evaluate(); if
heartbeats came from the main loop, every busy worker would look dead.
Liveness reporting must be independent of work — that's why it's a thread.
"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

# Make `evaluator` importable when run as a plain script from anywhere,
# without turning the repo into an installed package this early.
sys.path.append(str(Path(__file__).resolve().parents[1]))
from evaluator.pipeline import evaluate  # noqa: E402

COORD = os.environ.get("COORDINATOR_URL", "http://localhost:8000")
# hostname-pid: unique enough locally, human-readable in logs. Inside a
# compose container, hostname == container id — so worker ids in /status
# double as `docker kill` targets. The chaos script relies on this.
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
POLL_S = 1.0     # polling beats push: workers stay connectionless/disposable
HEARTBEAT_S = 2  # a CONTRACT with the coordinator's 3-miss detector, not a
                 # free choice — drift here and healthy workers get reaped

# Shared between main thread (writes on claim/finish) and heartbeat thread
# (reads every beat). The lock makes each snapshot internally consistent —
# never "busy" paired with the PREVIOUS job's id.
_state_lock = threading.Lock()
_state = {"status": "idle", "job_id": None, "epoch": None}


def _set_state(status: str, job_id=None, epoch=None) -> None:
    with _state_lock:
        _state["status"] = status
        _state["job_id"] = job_id
        _state["epoch"] = epoch


def heartbeat_loop() -> None:
    while True:
        with _state_lock:
            payload = {"worker_id": WORKER_ID, **_state}
        try:
            requests.post(f"{COORD}/heartbeat", json=payload, timeout=5)
        except requests.RequestException:
            # Nothing to handle: a missed beat IS the signal, delivered by
            # its absence. What matters is that this thread NEVER dies — a
            # worker whose heartbeat thread crashed looks dead forever
            # while happily burning CPU on real work.
            pass
        time.sleep(HEARTBEAT_S)


def run_one() -> bool:
    """Claim and run a single job. Returns False if the queue was empty."""
    resp = requests.post(f"{COORD}/jobs/claim",
                         json={"worker_id": WORKER_ID}, timeout=10)
    if resp.status_code == 204:
        return False
    job = resp.json()
    print(f"[{WORKER_ID}] claimed {job['job_id']} ({job['task_id']}) "
          f"epoch={job['epoch']}", flush=True)
    _set_state("busy", job["job_id"], job["epoch"])
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / f"{job['task_id']}.c"
            src.write_text(job["source_code"])
            result = evaluate(src)  # minutes of docker work happens here

        resp = requests.post(f"{COORD}/jobs/{job['job_id']}/result",
                             json={"worker_id": WORKER_ID,
                                   "result": result.to_dict()},
                             timeout=10)
        if resp.status_code == 409:
            # We were declared dead (slow, not dead — the detector's false
            # positive), the job was requeued, and our result is unwanted.
            # Correct behavior is to shrug: someone else owns it now.
            print(f"[{WORKER_ID}] result for {job['job_id']} rejected: "
                  f"job was reassigned while we worked", flush=True)
            return True
        resp.raise_for_status()
        print(f"[{WORKER_ID}] finished {job['job_id']}: "
              f"{result.verdict.value}", flush=True)
        return True
    finally:
        _set_state("idle")  # even if reporting failed — never beat "busy"
                            # for a job we are no longer working on


def main() -> None:
    print(f"[{WORKER_ID}] online, coordinator = {COORD}", flush=True)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    while True:
        try:
            if not run_one():
                time.sleep(POLL_S)
        except requests.RequestException as exc:
            # Coordinator down or flaky network: keep retrying, never die.
            print(f"[{WORKER_ID}] coordinator unreachable ({exc}); retrying",
                  flush=True)
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
