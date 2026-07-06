/* 06_infinite_loop: never terminates. Not a memory bug — a liveness bug,
 * and the most common way LLM code misbehaves in practice.
 *
 * This must NOT hang the pipeline: the in-container `timeout` kills it
 * after RUN_TIMEOUT_S and exit code 124 becomes verdict "timeout".
 * Remember --cpus=0.5: this pegs half a core for 10s and your laptop
 * shouldn't even notice. Foreshadowing week 3: a worker stuck on this
 * job looks EXACTLY like a dead worker unless something enforces time. */
int main(void) {
    volatile int keep_going = 1; /* volatile: compiler may not optimize the
                                    loop away — it must really spin */
    while (keep_going) {
        /* spin forever */
    }
    return 0;
}
