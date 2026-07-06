/* 05_oob_index: reads past the end of a STACK array.
 *
 * Same class of mistake as 02, different memory region — and a different
 * tool catches it. The array's size (int [5]) is part of its type, so
 * UBSan can check the index against the declared bound before the read
 * even happens. The heap case (02) had no declared bound, which is why it
 * needed ASan's poisoned guard zones instead. Natively this just reads
 * whatever garbage is on the stack at that offset and exits 0. */
#include <stdio.h>

int main(void) {
    int primes[5] = {2, 3, 5, 7, 11};
    int idx = 7;                      /* BUG: valid indexes are 0..4 */
    printf("prime=%d\n", primes[idx]);
    return 0;
}
