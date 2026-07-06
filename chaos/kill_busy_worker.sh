#!/usr/bin/env bash
# Chaos: find a BUSY worker via /status and docker-kill it, then watch the
# coordinator's events feed for detection + requeue. Compose mode only —
# worker ids are "<container-id>-<pid>", so the id prefix is a kill target.
set -euo pipefail

COORD=${COORDINATOR_URL:-http://localhost:8000}

BUSY=$(curl -s "$COORD/status" | python3 -c "
import sys, json
ws = json.load(sys.stdin)['workers']
busy = [w for w in ws if w['state'] == 'alive' and w['status'] == 'busy']
print(busy[0]['worker_id'] if busy else '')")

if [ -z "$BUSY" ]; then
    echo "no busy worker to kill — submit some jobs first"
    exit 1
fi

CONTAINER=${BUSY%-*}   # strip the "-<pid>" suffix; hostname == container id
echo "killing busy worker $BUSY (container $CONTAINER) at $(date +%T)"
docker kill "$CONTAINER" >/dev/null

echo "watching for detection (budget ~6s)..."
for i in $(seq 1 15); do
    DEAD=$(curl -s "$COORD/events?limit=5" | python3 -c "
import sys, json
evs = json.load(sys.stdin)
print(any(e['event'] == 'worker_dead' and e['worker_id'] == '$BUSY' for e in evs))")
    if [ "$DEAD" = "True" ]; then
        echo "detected dead after ~${i}s of watching; recent events:"
        curl -s "$COORD/events?limit=3" | python3 -m json.tool
        exit 0
    fi
    sleep 1
done
echo "NOT detected within 15s — failure detection is broken"
exit 1
