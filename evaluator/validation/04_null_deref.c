/* 04_null_deref: writes through a NULL pointer.
 *
 * NULL is address 0, and operating systems deliberately never map page 0 —
 * precisely so that this bug CRASHES (SIGSEGV) instead of silently
 * corrupting. So unlike 02 and 03, this one fails its functional run too;
 * the sanitizer's job here is to turn a raw crash into a typed report
 * with an exact line number. */
#include <stdio.h>

int main(void) {
    int *p = NULL; /* points at address 0 on purpose */
    *p = 7;        /* BUG: store to address 0 — unmapped, guaranteed fault */
    printf("%d\n", *p);
    return 0;
}
