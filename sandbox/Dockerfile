# gcc:13 is Debian with GCC 13 preinstalled. One base covers three of our
# four tools: the compiler, AddressSanitizer, and UBSan — the sanitizers
# ship inside GCC itself (they're compiler flags, not separate programs).
FROM gcc:13-bookworm

# cppcheck is the one tool GCC doesn't include: static analysis, i.e. it
# finds bugs by reading the source, never executing it. Installed at build
# time because --network=none means nothing can be installed at run time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cppcheck \
    && rm -rf /var/lib/apt/lists/*

# Non-root user even though --cap-drop=ALL already neuters root: defense in
# depth means no single layer is trusted to hold.
RUN useradd --create-home runner
USER runner

# /work is the job's scratch space. The image is frozen (--read-only at run
# time), so /work only becomes writable via a tmpfs mounted at run time.
WORKDIR /work
