/* Linked into the coverage builds (steps/build.sh, flavour cov). A program that ends by a signal (abort() after an
 * uncaught exception, SIGSEGV, the harness's SIGTERM on a timeout) never reaches the profile runtime's atexit hook,
 * so the coverage of exactly the runs that fail would be lost. This writes the profile first, then lets the signal
 * take its default course. */
#include <signal.h>
int __llvm_profile_write_file(void);
static void dfz_cov_on_signal(int sig) {
    __llvm_profile_write_file();
    signal(sig, SIG_DFL);
    raise(sig);
}
__attribute__((constructor)) static void dfz_cov_init(void) {
    int sigs[] = {SIGABRT, SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGTERM};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) signal(sigs[i], dfz_cov_on_signal);
}
