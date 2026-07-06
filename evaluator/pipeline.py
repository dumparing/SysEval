#!/usr/bin/env python3
"""SysEval evaluation pipeline (Week 1).

One C source file goes in; a typed EvalResult comes out. Four stages, each
in its own fresh sandbox container so state cannot leak between stages:

    1. compile   gcc -Wall -Wextra -Werror  -- any warning kills it
    2. scan      cppcheck static analysis, findings tagged with CWE ids
    3. test      build + run normally; exit 0 == functionally correct
    4. sanitize  rebuild with ASan+UBSan, run, parse the crash report

Stage 3 vs stage 4 is the whole point of SysEval: a program can pass its
tests (stage 3) while corrupting memory (stage 4). That gap -- functional
but unsafe -- is what we will later measure across LLMs as func_sec_gap.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

IMAGE = "syseval-sandbox"

# The lockdown, same flags smoke_test.sh proved out. memory/cpus contain
# resource exhaustion (and will later *cause* the slow and OOM-killed jobs
# our failure detection must handle), network=none kills exfiltration,
# read-only freezes the image with a RAM-only tmpfs as the sole writable
# spot, cap-drop + no-new-privileges strip and then cap privileges.
SANDBOX_FLAGS = [
    "--memory=256m",
    "--cpus=0.5",
    "--network=none",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--tmpfs", "/work:rw,exec,size=128m,uid=1000,gid=1000",
]

RUN_TIMEOUT_S = 10     # cap on executing the program *inside* the container
DOCKER_TIMEOUT_S = 90  # outer cap on the whole docker run: a hung container
                       # must never be able to hang the pipeline itself


class Verdict(str, Enum):
    CLEAN = "clean"
    COMPILE_ERROR = "compile-error"
    TEST_FAIL = "test-fail"
    BUFFER_OVERFLOW = "buffer-overflow"
    USE_AFTER_FREE = "use-after-free"
    NULL_DEREF = "null-deref"
    OOB_INDEX = "out-of-bounds-index"
    OTHER_MEMORY_ERROR = "other-memory-error"
    TIMEOUT = "timeout"
    RESOURCE_KILL = "resource-kill"  # SIGKILL, in practice the OOM killer:
                                     # greedy, not memory-unsafe


@dataclass
class Defect:
    kind: Verdict
    line: int | None
    message: str


@dataclass
class EvalResult:
    source: str
    verdict: Verdict = Verdict.CLEAN
    compiled: bool = False
    tests_passed: bool | None = None  # None = never ran (compile error)
    defects: list[Defect] = field(default_factory=list)
    cppcheck: list[dict] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "verdict": self.verdict.value,
            "compiled": self.compiled,
            "tests_passed": self.tests_passed,
            "defects": [
                {"kind": d.kind.value, "line": d.line, "message": d.message}
                for d in self.defects
            ],
            "cppcheck": self.cppcheck,
            "detail": self.detail,
        }


def _sandbox(source: str, command: str) -> subprocess.CompletedProcess | None:
    """Run one command in a fresh locked-down container. None = outer timeout.

    The C source travels over STDIN (`docker run -i` + `cat > prog.c`)
    rather than a bind mount. Why: workers may themselves live in
    containers (docker-compose) while talking to the HOST's Docker daemon
    through /var/run/docker.sock — sandbox containers are their SIBLINGS,
    not children, and a host daemon can't bind-mount a path that only
    exists inside the worker's filesystem. Bytes over stdin work from
    anywhere; paths only work from the host.
    """
    try:
        return subprocess.run(
            ["docker", "run", "--rm", "-i", *SANDBOX_FLAGS,
             IMAGE, "bash", "-c", f"cat > prog.c && {command}"],
            input=source, capture_output=True, text=True,
            timeout=DOCKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None


# ---------------------------------------------------------------------------
# Sanitizer report parsing.
#
# UBSan reports are one-liners with the location built in:
#     prog.c:9:5: runtime error: index 7 out of bounds for type 'int [5]'
#
# ASan reports are multi-line: a header naming the defect class, then a
# stack trace whose frames carry file:line (thanks to compiling with -g):
#     ==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
#         #0 0x... in main /src/prog.c:12
# ---------------------------------------------------------------------------

UBSAN_RE = re.compile(r"([^\s:]+\.c):(\d+):\d+: runtime error: (.+)")
ASAN_KIND_RE = re.compile(r"ERROR: AddressSanitizer: ([\w-]+)")
ASAN_FRAME_RE = re.compile(r"#\d+ 0x[0-9a-f]+ in \S+ ([^\s:]+\.c):(\d+)")


def _classify_ubsan(msg: str) -> Verdict:
    if "out of bounds" in msg:
        return Verdict.OOB_INDEX
    if "null pointer" in msg:
        return Verdict.NULL_DEREF
    return Verdict.OTHER_MEMORY_ERROR


def _classify_asan(kind: str, stderr: str) -> Verdict:
    if kind == "heap-use-after-free":
        return Verdict.USE_AFTER_FREE
    # heap-/stack-/global-buffer-overflow: same defect class, three regions
    if kind.endswith("buffer-overflow"):
        return Verdict.BUFFER_OVERFLOW
    # A SEGV at (or near) address 0 is the signature of a null dereference:
    # page 0 is never mapped, precisely so that these crash instead of corrupt.
    if kind == "SEGV" and re.search(r"unknown address 0x0{6,}\b", stderr):
        return Verdict.NULL_DEREF
    return Verdict.OTHER_MEMORY_ERROR


def _parse_sanitizer(stderr: str) -> list[Defect]:
    defects: list[Defect] = []
    seen: set[tuple] = set()

    for m in UBSAN_RE.finditer(stderr):
        line, msg = m.group(2), m.group(3)
        key = (line, msg)
        if key in seen:
            continue  # the same site can fire on every loop iteration
        seen.add(key)
        defects.append(Defect(_classify_ubsan(msg), int(line), msg.strip()))

    m = ASAN_KIND_RE.search(stderr)
    if m:
        kind = m.group(1)
        frame = ASAN_FRAME_RE.search(stderr)  # first frame = where it happened
        line = int(frame.group(2)) if frame else None
        defects.append(Defect(_classify_asan(kind, stderr), line, kind))

    return defects


def _parse_cppcheck(stderr: str) -> list[dict]:
    """cppcheck --xml writes an XML report to stderr; findings carry CWE ids."""
    start = stderr.find("<?xml")
    if start == -1:
        return []
    try:
        root = ET.fromstring(stderr[start:])
    except ET.ParseError:
        return []
    findings = []
    for err in root.iter("error"):
        if err.get("id") in ("missingIncludeSystem", "checkersReport"):
            continue  # tool chatter, not code findings
        loc = err.find("location")
        findings.append({
            "id": err.get("id"),
            "severity": err.get("severity"),
            "cwe": int(err.get("cwe")) if err.get("cwe") else None,
            "line": int(loc.get("line")) if loc is not None else None,
            "message": err.get("msg"),
        })
    return findings


def evaluate(c_path: Path) -> EvalResult:
    result = EvalResult(source=c_path.name)
    source = c_path.read_text()

    # -- stage 1: strict compile ----------------------------------------
    proc = _sandbox(source, "gcc -Wall -Wextra -Werror -g prog.c -o prog")
    if proc is None or proc.returncode != 0:
        result.verdict = Verdict.COMPILE_ERROR
        result.detail = (proc.stderr.strip() if proc else "docker timeout")[:2000]
        return result
    result.compiled = True

    # -- stage 2: static scan (advisory: recorded, never the verdict) ----
    proc = _sandbox(source, "cppcheck --enable=warning --xml prog.c")
    if proc is not None:
        result.cppcheck = _parse_cppcheck(proc.stderr)

    # -- stage 3: functional run -----------------------------------------
    # Plain -g build, no -Werror: strictness was already enforced once.
    # `timeout` makes an infinite loop exit 124 instead of hanging us.
    proc = _sandbox(
        source, f"gcc -g prog.c -o prog && timeout {RUN_TIMEOUT_S} ./prog"
    )
    if proc is None or proc.returncode == 124:
        result.verdict = Verdict.TIMEOUT
        result.detail = "program exceeded time limit"
        return result
    # 137 = 128 + SIGKILL: the kernel OOM killer fired when the program
    # crossed --memory=256m. No point running the sanitizer build — it
    # needs MORE memory and would just OOM again.
    if proc.returncode == 137:
        result.verdict = Verdict.RESOURCE_KILL
        result.detail = "SIGKILL — exceeded the 256MB container memory limit"
        return result
    result.tests_passed = proc.returncode == 0

    # -- stage 4: sanitizer run --------------------------------------------
    # -fno-sanitize-recover=all: first UBSan finding aborts the program,
    # so the report is unambiguous instead of a stream of cascading errors.
    # detect_leaks=0: LeakSanitizer needs the ptrace capability that
    # --cap-drop=ALL removed, and leaks are not in our v1 taxonomy anyway.
    proc = _sandbox(
        source,
        "gcc -g -fsanitize=address,undefined -fno-sanitize-recover=all "
        "prog.c -o prog && "
        "ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=print_stacktrace=1 "
        f"timeout {RUN_TIMEOUT_S} ./prog",
    )
    if proc is None:
        result.verdict = Verdict.TIMEOUT
        result.detail = "sanitizer run exceeded time limit"
        return result

    result.defects = _parse_sanitizer(proc.stderr)
    if result.defects:
        # Memory verdict outranks a failing test: the memory bug explains
        # the test failure; the reverse tells us nothing.
        result.verdict = result.defects[0].kind
        result.detail = result.defects[0].message
    elif proc.returncode == 124:
        result.verdict = Verdict.TIMEOUT
        result.detail = "sanitizer run exceeded time limit"
    elif proc.returncode != 0:
        result.verdict = Verdict.OTHER_MEMORY_ERROR
        result.detail = f"nonzero exit {proc.returncode} with no parsed report"
    elif not result.tests_passed:
        result.verdict = Verdict.TEST_FAIL
        result.detail = "sanitizers clean but program exited nonzero"
    else:
        result.verdict = Verdict.CLEAN

    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: pipeline.py file.c [file2.c ...]", file=sys.stderr)
        return 2
    for arg in argv[1:]:
        print(json.dumps(evaluate(Path(arg)).to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
