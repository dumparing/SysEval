/* 02_heap_overflow: writes one element past the end of a malloc'd buffer.
 *
 * malloc(n) asks the allocator for n bytes on the HEAP — memory you manage
 * by hand, alive until you free() it. C never checks array bounds: buf[5]
 * on a 5-slot buffer just writes to whatever bytes sit after it. Natively
 * that usually corrupts something silently and the program still "works" —
 * this file should PASS its functional run and FAIL the sanitizer run.
 * That combination — functional but unsafe — is the gap SysEval measures. */
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int n = 5;
    int *buf = malloc(n * sizeof(int)); /* 5 ints; valid indexes 0..4 */
    if (buf == NULL) {                  /* malloc returns NULL when out of
                                           memory — always check */
        return 1;
    }
    for (int i = 0; i <= n; i++) {      /* BUG: <= runs i = 0,1,2,3,4,5 */
        buf[i] = i * i;                 /* buf[5] lands past the end */
    }
    printf("filled %d slots\n", n);
    free(buf);                          /* heap memory must be freed by hand */
    return 0;
}
