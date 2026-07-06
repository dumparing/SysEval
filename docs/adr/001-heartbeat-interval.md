# ADR-001: Heartbeat interval 2s, death after 3 missed beats

**Status:** accepted (2026-07-06)

## Context

Workers die silently: SIGKILL, OOM, power loss give no goodbye. The
coordinator can only observe *silence*, and silence is ambiguous — dead, or
slow? Any detector built on silence is a guess with two tunable costs:

- **Detection latency** — how long a real death strands its job. Until
  detection fires, the job is invisible to recovery and the cluster is
  effectively smaller.
- **False positives** — declaring a live worker dead. Cost: the job runs
  twice (wasted compute), and a duplicate/stale result arrives later that
  must be rejected (handled: worker_id check now, epoch fencing in Week 5).

These trade against each other and cannot both be eliminated. The choice is
a tuning, not a solution.

## Decision

Workers heartbeat every **2s**; the reaper declares death after **3 missed
beats (~6s of silence)** and requeues in-flight jobs immediately. The
reaper sweeps every 1s, so worst-case detection ≈ 7s (6s threshold + 1
sweep period).

## Why these numbers

Our jobs take ~15–40s (four sandbox container stages at 0.5 CPU, with 10s
in-container run caps). A ~6–7s detection latency is well under one job
duration — a killed worker's job restarts before a human would notice —
while 3 consecutive misses tolerates the ordinary lies of a busy laptop:
GC pauses, scheduler hiccups, Docker Desktop contention, a worker briefly
starved by four sandbox containers compiling at once.

- **200ms / 1 miss:** detection in ~0.2s, but any hiccup >200ms kills a
  healthy worker. On a laptop running 4 workers × sandbox containers,
  sub-second pauses are *routine* — the cluster would thrash with false
  deaths, every one spawning a duplicate execution.
- **30s / 10 misses:** ~5 minutes to notice a death — ~10 job durations of
  stranded work, unacceptable when the whole benchmark is ~450 × 30s jobs.
- **Asymmetric middle (2s / 3):** interval short enough that misses are
  meaningful, threshold requiring *consecutive* misses so single blips are
  free. Measured on this machine: detections at 5.8s, 6.3s, 6.9s.

## Alternatives considered

- **Pull (coordinator probes workers):** requires workers to be addressable
  servers; ours are connectionless polling clients. Push keeps them dumb.
- **Claim-time leases with TTL:** equivalent power, per-job instead of
  per-worker; chosen against because per-worker liveness also feeds the
  Week 6 dashboard and multi-job futures.
- **TCP keepalive / connection tracking:** ties liveness to a transport
  connection we deliberately don't hold open.

## Consequences

- A worker paused >6s (SIGSTOP test, laptop sleep) is falsely declared
  dead; its job runs twice. At-least-once execution is now the system's
  contract, which *requires* idempotent result handling and stale-write
  rejection — worker_id check today, epoch fencing in Week 5 (the epoch
  counter is already bumped on every requeue).
- Detection depends on the reaper thread staying alive; it deliberately
  swallows Redis connection errors rather than crashing.
- Wall-clock timestamps (comparable across processes, survive restarts)
  mean an NTP jump could mass-kill or mass-revive workers. Accepted for a
  single-machine cluster.
