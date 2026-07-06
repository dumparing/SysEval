/* 01_clean: correct C — the control sample. Every stage should pass.
 *
 * C in 30 seconds: #include pulls in a library's declarations (stdio.h =
 * printf and friends). main() is the entry point; its return value is the
 * program's exit code, and 0 means success — that's our "tests pass" signal
 * for now. */
#include <stdio.h>

#define LEN 5 /* compile-time constant; C arrays don't know their own length,
                 so the programmer must carry it around by hand */

int main(void) {
    int nums[LEN] = {2, 4, 6, 8, 10}; /* array on the stack: freed automatically */
    int sum = 0;
    for (int i = 0; i < LEN; i++) {   /* i < LEN keeps every access in bounds */
        sum += nums[i];
    }
    printf("sum=%d\n", sum);
    return 0;
}
