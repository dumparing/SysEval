#!/usr/bin/env bash
# Smoke test: prove the sandbox can compile and run C under the FULL
# lockdown flags. If this passes, the pipeline can trust the sandbox.
set -euo pipefail

cd "$(dirname "$0")"

echo "== building image =="
docker build -t syseval-sandbox .

echo "== preparing test program =="
TESTDIR="$(mktemp -d)"
trap 'rm -rf "$TESTDIR"' EXIT
cat > "$TESTDIR/hello.c" <<'EOF'
#include <stdio.h>
int main(void) {
    printf("sandbox alive\n");
    return 0;
}
EOF

echo "== compiling + running inside locked-down container =="
# The full incantation. Two additions to the flags you already know:
#   -v ...:/src:ro    source code enters the box read-only (the only door in)
#   --tmpfs /work     RAM scratch disk, the only writable spot; "exec" because
#                     Docker's tmpfs default is noexec, which would block
#                     running the binary we just compiled; uid/gid=1000 because
#                     the daemon mounts it as root — without this, our own
#                     non-root "runner" user can't write to its own workspace
docker run --rm \
    --memory=256m \
    --cpus=0.5 \
    --network=none \
    --read-only \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    --tmpfs /work:rw,exec,size=128m,uid=1000,gid=1000 \
    -v "$TESTDIR":/src:ro \
    syseval-sandbox \
    bash -c 'cp /src/hello.c . && gcc -Wall -Wextra -Werror hello.c -o hello && ./hello'

echo "== sanity: prove the walls are real =="
# Each of these SHOULD fail; the test fails if any succeeds.
echo "-- network should be dead --"
docker run --rm --network=none syseval-sandbox \
    bash -c 'getent hosts example.com' && { echo "FAIL: network alive"; exit 1; }
echo "-- filesystem should be frozen --"
docker run --rm --read-only --tmpfs /work:rw,exec syseval-sandbox \
    bash -c 'touch /usr/local/owned' && { echo "FAIL: fs writable"; exit 1; }

echo "== smoke test PASSED =="
