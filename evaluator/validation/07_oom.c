/* 07_oom: allocates memory forever — the malloc-loop from our threat model.
 *
 * The kernel's OOM killer terminates the process with SIGKILL the moment
 * the container crosses --memory=256m; there is no warning and no chance
 * to clean up. Exit code becomes 137 (128 + signal 9). memset matters:
 * malloc alone only *reserves* address space; touching the pages is what
 * actually consumes RAM and trips the limit.
 * Foreshadowing week 3: this is the "job vanished mid-flight" failure our
 * coordinator must detect and reassign — nothing reports the death. */
#include <stdlib.h>
#include <string.h>

int main(void) {
    while (1) {
        char *chunk = malloc(8 * 1024 * 1024); /* 8 MB per lap */
        if (chunk == NULL) {
            return 1;               /* allocator gave up before the kernel did */
        }
        memset(chunk, 1, 8 * 1024 * 1024);      /* touch every page for real */
    }
}
