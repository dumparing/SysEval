/* 03_use_after_free: reads memory after free() has returned it.
 *
 * free() hands the bytes back to the allocator, but the pointer variable
 * still holds the old address — a "dangling pointer". The old bytes are
 * often still sitting there, so the read "works"... until the allocator
 * reuses that memory for something else and the value silently changes.
 * Like 02, this should pass its functional run and fail the sanitizer.
 *
 * The free is hidden behind a function call ON PURPOSE: gcc's
 * -Wuse-after-free catches free-then-use within one function at compile
 * time (our first version died on -Werror!), but it can't see across a
 * call boundary. That's exactly how real UAFs survive compilation — and
 * why runtime sanitizers exist at all. */
#include <stdio.h>
#include <stdlib.h>

/* In real code this is "cleanup()" in some other file — the caller has no
 * idea the pointer it still holds just went stale. */
static void release(int *p) {
    free(p);
}

int main(void) {
    int *score = malloc(sizeof(int));
    if (score == NULL) {
        return 1;
    }
    *score = 42;                   /* '*' dereferences: write 42 AT the address */
    release(score);                /* memory returned; score now dangles */
    printf("score=%d\n", *score);  /* BUG: read through the dangling pointer */
    return 0;
}
