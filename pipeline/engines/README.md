# Engines

One file per fuzzer. A file defines, in bash:

| Name | Meaning |
|---|---|
| `ENGINE_HARNESS` | which harness build it drives (`bin/harness_<name>`); several engines can share one |
| `engine_available` | succeeds when the fuzzer is installed |
| `engine_build <flags> <sources> <out> <libs>` | compile the harness for this engine (`return 0` when it reuses another engine's build) |
| `engine_run <harness> <seeds> <out> <ncpu> <seconds> <max_len> <min_len> <dict>` | start the fuzzer in the background, everything under `<out>` |
| `engine_stats <out>` | print `execs=<n> corpus=<n>` |
| `engine_corpus <out>` | print the corpus directories |

The harness gives every engine the same entry point, `LLVMFuzzerTestOneInput`, and one of three feedback routes
(`harness/fuzz_entry.cpp`): AFL++'s map (`-DDFZ_ENGINE_AFL`), libFuzzer's extra counters (`-DDFZ_ENGINE_LIBFUZZER`),
or trace-pc-guard callbacks (`-DDFZ_ENGINE_PCGUARD`). To add a fuzzer, add a file here; `steps/build.sh`,
`steps/campaign.sh` and the reports pick it up by its file name.

| Engine | Fuzzer | Feedback |
|---|---|---|
| `afl` | AFL++ `afl-fuzz`, one main and N-1 secondaries, seven power schedules, MOpt on a quarter | both programs' edges + difference signatures |
| `afl-blind` | the same with `DFZ_NOFEEDBACK=1` | none (black-box baseline) |
| `libafl` | LibAFL `libafl-fuzz` | as `afl` |
| `libfuzzer` | LLVM libFuzzer, fork mode | as `afl`, through extra counters |
| `hfuzz` | honggfuzz, persistent mode | as `afl`, through trace-pc-guard |
