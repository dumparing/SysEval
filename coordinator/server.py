"""SysEval coordinator (Week 3: heartbeats, failure detection, requeue).

Week-2 recap — why a coordinator instead of `for f in files: evaluate(f)`:
parallelism (450 x ~30s ≈ 4h serial), failure isolation, queue durability
outside any process, and a single authority over claims. Redis stays dumb
transport (a list, sets, hashes); every decision is made HERE.

New in Week 3: the coordinator finally knows workers exist. Each worker
sends a heartbeat every HEARTBEAT_S seconds; a background reaper thread
declares any worker silent for MISS_THRESHOLD beats dead and requeues its
in-flight jobs. Two truths to hold at once:

  1. Death cannot be observed remotely — only SILENCE can, and silence has
     two causes: dead, or slow. So detection is a guess with a tunable
     false-positive rate (see docs/adr/001-heartbeat-interval.md).
  2. Requeueing on a guess means a job can run TWICE (the "dead" worker
     was slow and finishes anyway). We chose at-least-once: losing work is
     worse than repeating it. The epoch counter bumped on every requeue is
     Week 5's hook for making duplicates harmless (fencing); it is
     recorded now, not yet enforced.
"""
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import redis
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

# --- failure-detection tuning (the WHOLE tuning surface; ADR-001) ---------
HEARTBEAT_S = 2         # workers ping this often
MISS_THRESHOLD = 3      # this many silent intervals = declared dead
DEAD_AFTER_S = HEARTBEAT_S * MISS_THRESHOLD   # ~6s of silence = "dead"
REAPER_PERIOD_S = 1     # how often the reaper sweeps; must be << DEAD_AFTER_S
                        # or detection latency silently grows past the ADR

r = redis.Redis(host=os.environ.get("REDIS_HOST", "localhost"),
                port=6379, decode_responses=True)

PENDING = "jobs:pending"    # Redis LIST: the queue. LPUSH + RPOP = FIFO.
RUNNING = "jobs:running"    # Redis SET: job ids currently claimed
DONE = "jobs:done"          # Redis SET: job ids finished
WORKERS = "workers:known"   # Redis SET: every worker id that ever heartbeat
EVENTS = "events"           # Redis LIST: newest-first log of cluster events


def jkey(job_id: str) -> str:
    return f"job:{job_id}"


def wkey(worker_id: str) -> str:
    return f"worker:{worker_id}"


def log_event(kind: str, **fields) -> None:
    """Cluster events (deaths, requeues, rejections) — one place, twice:
    Redis for machines (Week 6 dashboard feed), stdout for humans/demos."""
    entry = {"ts": time.time(), "event": kind, **fields}
    r.lpush(EVENTS, json.dumps(entry))
    r.ltrim(EVENTS, 0, 999)  # keep the last 1000; this is a feed, not an archive
    print(f"EVENT {kind} {fields}", flush=True)


# --- the reaper: failure detection + recovery ------------------------------

def _requeue_inflight(worker_id: str) -> None:
    """Give a dead worker's claimed jobs back to the queue.

    Source of truth is OUR record (RUNNING set + job hashes, written at
    claim time) — never the worker's last heartbeat. A worker can die
    after claiming but before its next beat; trusting heartbeat contents
    would strand exactly those jobs.
    """
    for job_id in r.smembers(RUNNING):
        job = r.hgetall(jkey(job_id))
        if job.get("worker_id") != worker_id or job.get("status") != "running":
            continue
        # Epoch bump BEFORE requeue: anyone holding the old assignment now
        # holds a stale epoch. Week 5 makes the coordinator enforce that;
        # recording it costs nothing today and makes the history auditable.
        new_epoch = r.hincrby(jkey(job_id), "epoch", 1)
        r.hset(jkey(job_id), mapping={"status": "pending", "worker_id": ""})
        r.srem(RUNNING, job_id)
        r.lpush(PENDING, job_id)
        log_event("job_requeued", job_id=job_id, from_worker=worker_id,
                  epoch=new_epoch)


def _reap_once() -> None:
    now = time.time()
    for worker_id in r.smembers(WORKERS):
        w = r.hgetall(wkey(worker_id))
        if not w or w.get("state") == "dead":
            continue  # already mourned; requeue happened at declaration time
        silent_for = now - float(w["last_seen"])
        if silent_for > DEAD_AFTER_S:
            # This is a GUESS. The worker may be slow, paused, or partitioned
            # — indistinguishable from dead by silence alone. We accept the
            # false-positive rate our 2s/3-miss tuning implies (ADR-001).
            r.hset(wkey(worker_id), "state", "dead")
            log_event("worker_dead", worker_id=worker_id,
                      silent_for=round(silent_for, 1))
            _requeue_inflight(worker_id)


def reaper_loop() -> None:
    while True:
        try:
            _reap_once()
        except redis.exceptions.ConnectionError:
            # Redis blip: detection pauses rather than crashing. A reaper
            # that dies takes failure detection with it — silently.
            pass
        time.sleep(REAPER_PERIOD_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Daemon thread: dies with the process, needs no shutdown handshake.
    threading.Thread(target=reaper_loop, daemon=True).start()
    yield


app = FastAPI(title="SysEval coordinator", lifespan=lifespan)


# --- request/response models -----------------------------------------------

class SubmitReq(BaseModel):
    task_id: str
    source_code: str


class ClaimReq(BaseModel):
    worker_id: str


class ResultReq(BaseModel):
    worker_id: str
    result: dict


class HeartbeatReq(BaseModel):
    worker_id: str
    status: str                      # "idle" | "busy"
    job_id: Optional[str] = None     # what it's chewing on, if busy
    epoch: Optional[int] = None      # echo of the claim's epoch (Week 5 prep)


# --- endpoints --------------------------------------------------------------

@app.post("/jobs")
def submit_job(req: SubmitReq):
    # uuid4 = random; no coordination needed to stay unique, unlike a counter
    job_id = uuid.uuid4().hex[:12]
    # Hash FIRST, queue second: the queue entry is a promise the hash exists.
    r.hset(jkey(job_id), mapping={
        "job_id": job_id,
        "task_id": req.task_id,
        "source_code": req.source_code,
        "status": "pending",
        "epoch": 0,                  # bumped on every (re)assignment
        "submitted_at": time.time(),
    })
    r.lpush(PENDING, job_id)
    return {"job_id": job_id}


@app.post("/jobs/claim")
def claim_job(req: ClaimReq):
    # RPOP is atomic (Redis executes commands one at a time), so two
    # workers polling simultaneously can never receive the same job.
    job_id = r.rpop(PENDING)
    if job_id is None:
        return Response(status_code=204)  # queue empty
    r.hset(jkey(job_id), mapping={
        "status": "running",
        "worker_id": req.worker_id,
        "claimed_at": time.time(),
    })
    r.sadd(RUNNING, job_id)
    job = r.hgetall(jkey(job_id))
    return {
        "job_id": job_id,
        "task_id": job["task_id"],
        "source_code": job["source_code"],
        "epoch": int(job.get("epoch", 0)),  # worker echoes this back;
                                            # Week 5 makes it a fencing token
    }


@app.post("/jobs/{job_id}/result")
def report_result(job_id: str, req: ResultReq):
    job = r.hgetall(jkey(job_id))
    if not job:
        raise HTTPException(404, "unknown job")
    # Week-3 trust model: only the currently-assigned worker may report.
    # After a requeue, worker_id is "" or someone else — so a slow-not-dead
    # worker's late result bounces here. This catches MOST stale writes but
    # not all (it's a check-then-act, not atomic); Week 5's epoch fencing
    # closes it properly.
    if job.get("worker_id") != req.worker_id:
        log_event("report_rejected", job_id=job_id, worker_id=req.worker_id,
                  reason="job not assigned to reporter")
        raise HTTPException(409, "job is not assigned to this worker")
    r.hset(jkey(job_id), mapping={
        "status": "done",
        "result": json.dumps(req.result),
        "finished_at": time.time(),
    })
    r.srem(RUNNING, job_id)
    r.sadd(DONE, job_id)
    return {"ok": True}


@app.post("/heartbeat")
def heartbeat(req: HeartbeatReq):
    was = r.hget(wkey(req.worker_id), "state")
    r.sadd(WORKERS, req.worker_id)
    r.hset(wkey(req.worker_id), mapping={
        "state": "alive",
        "status": req.status,
        "job_id": req.job_id or "",
        "epoch": req.epoch if req.epoch is not None else "",
        # Wall clock, not monotonic: it must survive coordinator restarts
        # (it lives in Redis) and be comparable across processes. The cost:
        # an NTP clock jump could mass-kill or mass-revive workers. Known,
        # accepted, documented — local-only cluster, one machine, one clock.
        "last_seen": time.time(),
    })
    if was == "dead":
        # False positive confessed: it was slow, not dead. Welcome back as
        # a worker — but its old job was already requeued, and any late
        # report will bounce off the worker_id check in report_result.
        log_event("worker_resurrected", worker_id=req.worker_id)
    return {"ok": True}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = r.hgetall(jkey(job_id))
    if not job:
        raise HTTPException(404, "unknown job")
    job.pop("source_code", None)  # bulky; fetch the verdict, not the program
    if "result" in job:
        job["result"] = json.loads(job["result"])
    return job


@app.get("/status")
def get_status():
    now = time.time()
    workers = []
    for worker_id in sorted(r.smembers(WORKERS)):
        w = r.hgetall(wkey(worker_id))
        if not w:
            continue
        workers.append({
            "worker_id": worker_id,
            "state": w.get("state"),
            "status": w.get("status"),
            "job_id": w.get("job_id") or None,
            "seconds_since_heartbeat": round(now - float(w["last_seen"]), 1),
        })
    return {
        "pending": r.llen(PENDING),
        "running": r.scard(RUNNING),
        "done": r.scard(DONE),
        "workers": workers,
    }


@app.get("/events")
def get_events(limit: int = 50):
    return [json.loads(e) for e in r.lrange(EVENTS, 0, limit - 1)]
