/* Linked into the coverage builds (steps/build.sh, flavour cov). A program that ends by a signal of its own (abort()
 * after an uncaught exception, SIGSEGV) never reaches the profile runtime's atexit hook, so the coverage of exactly
 * the runs that fail would be lost. This writes the profile first, then lets the signal take its default course.
 *
 * Not on SIGTERM, which is how the harness ends a run that passed the time limit: such a run is cut at an arbitrary
 * point with its threads in the middle of functions, and llvm-cov derives many counts from others (the code after
 * an early return = entries - returns), so every thread caught between an entry and its return shows up as one
 * execution of code that never ran. The same holds for the other threads of a program that crashes; that is
 * accepted, because the crashing runs are the ones that cover the error paths. */
#include <signal.h>
int __llvm_profile_write_file(void);
static void dfz_cov_on_signal(int sig) {
    __llvm_profile_write_file();
    signal(sig, SIG_DFL);
    raise(sig);
}
__attribute__((constructor)) static void dfz_cov_init(void) {
    int sigs[] = {SIGABRT, SIGSEGV, SIGBUS, SIGFPE, SIGILL};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) signal(sigs[i], dfz_cov_on_signal);
}
