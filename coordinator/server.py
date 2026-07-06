"""SysEval coordinator (Week 2 baseline: queue + claim + report, no failure
handling yet — a worker that dies strands its job, ON PURPOSE, until Week 3).

Why a coordinator at all, instead of `for f in files: evaluate(f)`?
  1. Throughput: 450 jobs x ~30s each is ~4 hours serial. Independent jobs
     are embarrassingly parallel — but parallelism needs someone to hand out
     work without duplication.
  2. Failure isolation: in the loop version, one wedged job takes down the
     entire run. Here it takes down one worker, and only until Week 3's
     detection notices.
  3. Durability: the queue lives in Redis, not in any process's memory.
     Kill the coordinator, kill every worker — the remaining work is intact.

Redis is deliberately DUMB TRANSPORT: a list (the queue) and hashes (job
state). Every interesting decision — who may claim, what counts as done,
and later who is alive and which epoch wins — is made HERE. Swap Redis for
Postgres and the coordination logic wouldn't change. "We used Redis" is not
the interesting part of this system; the logic layered on top is.

Workers reach us over HTTP instead of touching Redis directly because
claims need a single authority. In Week 5 this server will REJECT writes
from workers holding stale epochs — impossible to enforce if N untrusted
worker processes write to storage themselves.
"""
import json
import time
import uuid

import redis
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

r = redis.Redis(host="localhost", port=6379, decode_responses=True)
app = FastAPI(title="SysEval coordinator")

PENDING = "jobs:pending"  # Redis LIST: the queue. LPUSH + RPOP = FIFO.
RUNNING = "jobs:running"  # Redis SET: job ids currently claimed
DONE = "jobs:done"        # Redis SET: job ids finished


def jkey(job_id: str) -> str:
    return f"job:{job_id}"


class SubmitReq(BaseModel):
    task_id: str
    source_code: str


class ClaimReq(BaseModel):
    worker_id: str


class ResultReq(BaseModel):
    worker_id: str
    result: dict


@app.post("/jobs")
def submit_job(req: SubmitReq):
    # uuid4 = random; no coordination needed to stay unique, unlike a counter
    job_id = uuid.uuid4().hex[:12]
    r.hset(jkey(job_id), mapping={
        "job_id": job_id,
        "task_id": req.task_id,
        "source_code": req.source_code,
        "status": "pending",
        "submitted_at": time.time(),
    })
    r.lpush(PENDING, job_id)
    return {"job_id": job_id}


@app.post("/jobs/claim")
def claim_job(req: ClaimReq):
    # THE line Week 2 exists to teach: Redis executes commands one at a
    # time, so RPOP is atomic — two workers polling simultaneously can
    # never receive the same job. Our no-duplicate-work guarantee rests
    # entirely on this property, not on any cleverness of ours.
    job_id = r.rpop(PENDING)
    if job_id is None:
        return Response(status_code=204)  # queue empty: "no content for you"
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
    }


@app.post("/jobs/{job_id}/result")
def report_result(job_id: str, req: ResultReq):
    job = r.hgetall(jkey(job_id))
    if not job:
        raise HTTPException(404, "unknown job")
    # Week-2 trust model: only the assigned worker may report. This is NOT
    # enough once jobs get REASSIGNED (Week 3): the old worker may still be
    # alive-but-slow and finish later. Fencing epochs (Week 5) fix that;
    # this check is the placeholder that will fail first.
    if job.get("worker_id") != req.worker_id:
        raise HTTPException(409, "job is not assigned to this worker")
    r.hset(jkey(job_id), mapping={
        "status": "done",
        "result": json.dumps(req.result),
        "finished_at": time.time(),
    })
    r.srem(RUNNING, job_id)
    r.sadd(DONE, job_id)
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
    return {
        "pending": r.llen(PENDING),
        "running": r.scard(RUNNING),
        "done": r.scard(DONE),
        # Week 3 adds: workers seen, last heartbeat per worker, deaths.
    }
